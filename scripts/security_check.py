import ast
import os
import sys

try:
    from scripts.bank_permissions import validate_read_only_permissions
    from scripts.paths import (
        PROJECT_ROOT,
        SEED_FILES,
        get_private_data_dir,
        private_data_dir_is_safe,
    )
    from scripts.plaid_environment import get_plaid_config
except ModuleNotFoundError:
    from bank_permissions import validate_read_only_permissions
    from paths import (
        PROJECT_ROOT,
        SEED_FILES,
        get_private_data_dir,
        private_data_dir_is_safe,
    )
    from plaid_environment import get_plaid_config


REQUIRED_IGNORE_RULES = {".env", ".env.*", "*.key", "*.pem", "*.p12", "data/*"}
SECRET_WORDS = {"KEY", "PASSWORD", "SECRET", "TOKEN"}
SOURCE_SUFFIXES = {".py", ".md", ".json", ".csv", ".txt", ".yml", ".yaml", ".toml"}
SKIP_FOLDERS = {".git", ".venv", "__pycache__"}
REQUIRED_SECURITY_HEADERS = {
    "Cache-Control",
    "Content-Security-Policy",
    "Referrer-Policy",
    "X-Content-Type-Options",
    "frame-ancestors 'none'",
    "form-action 'self'",
}


def get_app_host(app_path):  #Reads the saved HOST value without running the web app
    source = app_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "HOST":
                return ast.literal_eval(node.value)

    raise ValueError("HOST setting not found in app.py")


def get_browser_header_check(app_path):  #Checks the simple browser safety headers stay in place
    source = app_path.read_text(encoding="utf-8")
    missing = sorted(header for header in REQUIRED_SECURITY_HEADERS if header not in source)

    if missing:
        return False, f"Missing: {', '.join(missing)}"

    return True, "Security headers are present"


def get_named_function(tree, function_name):  #Finds one module function or handler method without importing app.py
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )


def function_calls(function_node, called_name):  #Checks one function routes through a named guard
    if function_node is None:
        return False

    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == called_name
        for node in ast.walk(function_node)
    )


def function_reads_header(function_node, header_constant):  #Checks a handler reads one exact named header
    if function_node is None:
        return False

    for node in ast.walk(function_node):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
            continue

        headers = node.func.value
        header_name = node.args[0]
        if (
            isinstance(headers, ast.Attribute)
            and isinstance(headers.value, ast.Name)
            and headers.value.id == "self"
            and headers.attr == "headers"
            and isinstance(header_name, ast.Name)
            and header_name.id == header_constant
        ):
            return True

    return False


def function_passes_header_to_call(
    function_node,
    called_name,
    header_constant,
):  #Checks one guard call receives the authenticated proxy header
    if function_node is None:
        return False

    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == called_name
        and function_reads_header(node, header_constant)
        for node in ast.walk(function_node)
    )


def get_request_guard_check(app_path):  #Checks reads and writes both pass through their authorization guards
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    read_guard = get_named_function(tree, "read_request_is_allowed")
    write_guard = get_named_function(tree, "write_request_is_allowed")
    get_handler = get_named_function(tree, "do_GET")
    post_handler = get_named_function(tree, "do_POST")
    missing = []

    if read_guard is None:
        missing.append("read guard")
    if write_guard is None:
        missing.append("write guard")
    if not function_calls(get_handler, "read_request_is_allowed"):
        missing.append("GET guard call")
    if not function_calls(post_handler, "write_request_is_allowed"):
        missing.append("POST guard call")
    if not function_calls(write_guard, "read_request_is_allowed"):
        missing.append("write-to-read authorization chain")

    if missing:
        return False, f"Missing: {', '.join(missing)}"

    return True, "GET and POST requests use the authorization guards"


def get_tailscale_identity_check(app_path):  #Checks both handlers pass Tailscale's authenticated login header
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    header_value = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "TAILSCALE_USER_HEADER"
            for target in node.targets
        ):
            try:
                header_value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                header_value = None
            break

    get_handler = get_named_function(tree, "do_GET")
    post_handler = get_named_function(tree, "do_POST")
    missing = []
    if header_value != "Tailscale-User-Login":
        missing.append("literal Tailscale-User-Login header")
    if not function_passes_header_to_call(
        get_handler,
        "read_request_is_allowed",
        "TAILSCALE_USER_HEADER",
    ):
        missing.append("GET identity header")
    if not function_passes_header_to_call(
        post_handler,
        "write_request_is_allowed",
        "TAILSCALE_USER_HEADER",
    ):
        missing.append("POST identity header")

    if missing:
        return False, f"Missing: {', '.join(missing)}"

    return True, "GET and POST requests pass the Tailscale user identity"


def get_ignore_rules(gitignore_path):  #Reads active gitignore rules
    lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    return {
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }


def get_source_files(project_root):  #Finds project text files that should never contain tokens
    source_files = []

    for path in project_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue

        relative_parts = path.relative_to(project_root).parts
        if any(part in SKIP_FOLDERS for part in relative_parts):
            continue

        source_files.append(path)

    return source_files


def get_environment_tokens():  #Finds Finance Hub secrets that should use encrypted files instead
    return {
        name: value
        for name, value in os.environ.items()
        if name.startswith("FINANCE_HUB_")
        and set(name.split("_")) & SECRET_WORDS
        and value
    }


def find_token_leaks(project_root):  #Checks configured token values are not saved in project files
    configured_tokens = get_environment_tokens()
    leaks = []

    for path in get_source_files(project_root):
        text = path.read_text(encoding="utf-8", errors="ignore")

        for variable_name, token_value in configured_tokens.items():
            if token_value in text:
                leaks.append((variable_name, str(path.relative_to(project_root))))

    return leaks


def get_bank_permissions():  #Reads future bank permissions without needing an API connection
    permission_text = os.getenv("FINANCE_HUB_BANK_PERMISSIONS", "")
    return [
        permission.strip()
        for permission in permission_text.split(",")
        if permission.strip()
    ]


def get_permission_check(permissions):  #Checks configured access against the shared read-only policy
    if not permissions:
        return True, "No bank permissions configured"

    try:
        approved = validate_read_only_permissions(permissions)
        return True, f"Approved: {', '.join(approved)}"
    except ValueError as error:
        return False, str(error)


def get_plaid_connection_mode_check():  #Confirms US connections always use the reviewed Production configuration
    config = get_plaid_config()
    return (
        config["name"] == "production",
        f"{config['label']} is the default connection mode",
    )


def find_project_private_files(project_root):  #Finds private files that still need moving out of OneDrive
    data_dir = project_root / "data"
    if not data_dir.exists():
        return []

    return [
        path
        for path in data_dir.rglob("*")
        if path.is_file()
        and not (path.parent == data_dir and path.name in SEED_FILES)
    ]


def run_checks(project_root=PROJECT_ROOT):  #Runs each security baseline check
    app_path = project_root / "app.py"
    app_host = get_app_host(app_path)
    browser_headers_are_safe, browser_header_details = get_browser_header_check(app_path)
    request_guards_are_safe, request_guard_details = get_request_guard_check(app_path)
    tailscale_identity_is_safe, tailscale_identity_details = get_tailscale_identity_check(app_path)
    ignore_rules = get_ignore_rules(project_root / ".gitignore")
    missing_rules = sorted(REQUIRED_IGNORE_RULES - ignore_rules)
    token_leaks = find_token_leaks(project_root)
    bank_permissions = get_bank_permissions()
    permissions_are_safe, permission_details = get_permission_check(bank_permissions)
    plaid_mode_is_safe, plaid_mode_details = get_plaid_connection_mode_check()
    private_data_dir = get_private_data_dir()
    private_files_in_project = find_project_private_files(project_root)
    environment_tokens = sorted(get_environment_tokens())

    return [
        (
            "Loopback-only web server",
            app_host == "127.0.0.1",
            f"HOST is {app_host}",
        ),
        (
            "Browser security headers",
            browser_headers_are_safe,
            browser_header_details,
        ),
        (
            "Read and write request guards",
            request_guards_are_safe,
            request_guard_details,
        ),
        (
            "Tailscale identity guard",
            tailscale_identity_is_safe,
            tailscale_identity_details,
        ),
        (
            "Secret files ignored",
            not missing_rules,
            "All required rules found"
            if not missing_rules
            else f"Missing: {', '.join(missing_rules)}",
        ),
        (
            "Tokens kept out of project files",
            not token_leaks,
            "No configured token values found"
            if not token_leaks
            else f"Found token values in {len(token_leaks)} file(s)",
        ),
        (
            "Bank tokens outside environment",
            not environment_tokens,
            "No bank tokens found in environment variables"
            if not environment_tokens
            else f"Remove: {', '.join(environment_tokens)}",
        ),
        (
            "Read-only bank permissions",
            permissions_are_safe,
            permission_details,
        ),
        (
            "Plaid connection mode",
            plaid_mode_is_safe,
            plaid_mode_details,
        ),
        (
            "Private data location",
            private_data_dir_is_safe(private_data_dir, project_root),
            f"Using {private_data_dir}",
        ),
        (
            "Private files outside project",
            not private_files_in_project,
            "No private files found in project"
            if not private_files_in_project
            else f"Move {len(private_files_in_project)} private file(s)",
        ),
    ]


def main():  #Prints the security baseline result
    try:
        checks = run_checks()
    except (OSError, SyntaxError, ValueError) as error:
        print(f"Could not run security checks: {error}", file=sys.stderr)
        return 1

    for name, passed, details in checks:
        result = "PASS" if passed else "FAIL"
        print(f"[{result}] {name}: {details}")

    if all(passed for _, passed, _ in checks):
        print("Security baseline passed")
        return 0

    print("Security baseline failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
