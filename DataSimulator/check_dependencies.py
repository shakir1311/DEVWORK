import os
import sys
import ast
import importlib.util
import importlib.metadata
import subprocess

# Mapping of import names to PyPI package names
PACKAGE_MAPPING = {
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "yaml": "PyYAML",
    "serial": "pyserial",
    "paho": "paho-mqtt",
    "paho.mqtt": "paho-mqtt",
    "amqtt": "amqtt",
    "PyQt6": "PyQt6",
    "pyqtgraph": "pyqtgraph",
    "scipy": "scipy", 
    "numpy": "numpy",
    # Add more mappings as needed
}

# Standard library modules to ignore
STD_LIB = set(sys.builtin_module_names) | {
    "os", "sys", "re", "json", "time", "datetime", "math", "random",
    "subprocess", "threading", "multiprocessing", "abc", "typing",
    "collections", "functools", "itertools", "logging", "socket",
    "argparse", "pathlib", "shutil", "glob", "csv", "xml", "html",
    "http", "urllib", "email", "io", "copy", "pickle", "struct",
    "platform", "venv", "ast", "inspect", "traceback", "signal",
    "pkg_resources", "importlib"
}
# Get a more complete list of stdlib
try:
    STD_LIB.update(sys.stdlib_module_names)
except AttributeError:
    pass # Python < 3.10

def get_imports_from_file(filepath):
    """Extracts top-level imports from a Python file."""
    imports = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read(), filename=filepath)
            except SyntaxError:
                return imports # Skip files with syntax errors

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0])
    except Exception as e:
        print(f"Warning: Could not parse {filepath}: {e}")
    return imports

def scan_directory(directory):
    """Scans a directory for Python files and collects all imports."""
    all_imports = set()
    for root, _, files in os.walk(directory):
        if "venv" in root or "__pycache__" in root:
            continue
        for file in files:
            if file.endswith(".py") and file != "check_dependencies.py": # Skip self
                filepath = os.path.join(root, file)
                all_imports.update(get_imports_from_file(filepath))
    return all_imports

def is_installed(package_name):
    """Checks if a package is installed using importlib.metadata."""
    # Check if we can import the module itself
    spec = importlib.util.find_spec(package_name)
    if spec is not None:
        return True
    
    # Check if the distribution is installed (mapped name)
    pypi_name = PACKAGE_MAPPING.get(package_name, package_name)
    try:
        importlib.metadata.distribution(pypi_name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False

def resolve_packages(imports):
    """Resolves import names to PyPI package names."""
    required_packages = set()
    for imp in imports:
        if imp in STD_LIB:
            continue
        
        if imp.startswith("_"): continue 
        if imp in ["setuptools", "pkg_resources", "pip"]: continue

        # We keep the original import name to check installation,
        # but for installation we need the PyPI name.
        # Let's verify if the import name is resolvable first.
        
        # If the import name is "paho.mqtt", we want to check if "paho-mqtt" is installed
        required_packages.add(imp)
    
    return required_packages

def install_packages(packages):
    """Installs a list of packages using pip."""
    if not packages:
        return True
        
    # Convert import names to PyPI names for installation
    install_list = [PACKAGE_MAPPING.get(pkg, pkg) for pkg in packages]
    
    print(f"📦 Installing missing packages: {', '.join(install_list)}...")
    cmd = [sys.executable, "-m", "pip", "install"] + install_list
    try:
        # Capture output to show on error
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(result.stdout)
        print("✅ Installation complete.")
        return True
    except subprocess.CalledProcessError as e:
        print("❌ Installation failed.")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        return False

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Dynamic Dependency Checker")
    parser.add_argument("--check", action="store_true", help="Check for missing dependencies")
    parser.add_argument("--install", action="store_true", help="Install missing dependencies")
    args = parser.parse_args()

    print("🔍 Scanning codebase for dependencies...")
    root_dir = os.path.dirname(os.path.abspath(__file__))
    raw_imports = scan_directory(root_dir)
    
    required_imports = resolve_packages(raw_imports)
    
    missing_packages = []
    print(f"   Found imports: {', '.join(sorted(raw_imports))}")
    
    for imp in required_imports:
        if not is_installed(imp):
            # One more check: maybe it's a sub-package of something installed?
            # e.g. 'serial.tools' might be from 'pyserial'
            # But assume mapping handles the main package names.
            print(f"  ❌ {imp} (package: {PACKAGE_MAPPING.get(imp, imp)}) not found")
            missing_packages.append(imp)
        else:
            # print(f"  ✅ {imp} found")
            pass

    if not missing_packages:
        print("✅ All dependencies appear to be satisfied.")
        sys.exit(0)
    
    if args.check:
        print(f"⚠️  Missing dependencies: {', '.join(missing_packages)}")
        sys.exit(1) # Return error code so script knows to try install

    if args.install:
        if install_packages(missing_packages):
            sys.exit(0)
        else:
            sys.exit(1)

if __name__ == "__main__":
    main()
