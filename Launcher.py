# Combined Unified Manager

import os
import subprocess
from pathlib import Path
from typing import List
import json
import sys

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(70)}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.END}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")


def print_warning(text: str):
    print(f"{Colors.WARNING}⚠️  {text}{Colors.END}")


def print_error(text: str):
    print(f"{Colors.RED}❌ {text}{Colors.END}")


def print_info(text: str):
    print(f"{Colors.BLUE}💡 {text}{Colors.END}")


class HybridBlackBotLauncher:
    """
    Hybrid launcher that combines:
    1. Unified BlackBoT Manager (configuration & multi-instance)
    2. Enhanced environment setup (venv, deps, validation)
    3. One-time migration from legacy settings.py to instances/* via migrate.py
    """

    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir).resolve()

        possible_venvs = ["environment", ".venv", "venv"]
        self.venv_dir = None
        for name in possible_venvs:
            candidate = self.base_dir / name
            if candidate.exists():
                self.venv_dir = candidate
                break
        if self.venv_dir is None:
            # Default: .venv (mai „standard” și în IDE-uri)
            self.venv_dir = self.base_dir / ".venv"

        if os.name == "nt":  # Windows
            self.venv_bin = self.venv_dir / "Scripts"
            self.python_exec = self.venv_bin / "python.exe"
            self.pip_exec = self.venv_bin / "pip.exe"
        else:  # Linux/macOS
            self.venv_bin = self.venv_dir / "bin"
            self.python_exec = self.venv_bin / "python"
            self.pip_exec = self.venv_bin / "pip"

        # Detect available systems
        self.has_unified_manager = (self.base_dir / "Manager.py").exists()
        self.has_blackbot = (self.base_dir / "BlackBoT.py").exists()

        # Required and optional packages
        self.required_packages = [
            "twisted",
            "pyopenssl",
            "service_identity",
            "psutil",
            "scapy",
            "bcrypt",
            "watchdog",
            "requests",
            "pyyaml" ,
            "fastapi",
            "uvicorn",
            "pydantic"
        ]
        self.optional_packages = [
            "python-dotenv",  # Enhanced .env support
        ]
        print_info(f"🔧 Hybrid BlackBoT Launcher initialized")
        print_info(f"📁 Base directory: {self.base_dir}")
        print_info(f"🤖 Unified manager: {'✅' if self.has_unified_manager else '❌'}")
        print_info(f"🐍 Virtual env: {'✅' if self.venv_dir.exists() else '❌'} ({self.venv_dir})")

    # -------------------------------------------------------------------------
    # ENVIRONMENT SETUP
    # -------------------------------------------------------------------------
    def check_python_environment(self) -> bool:
        """Check and setup Python environment like enhanced run script"""
        print_info("🐍 Checking Python environment...")

        # Check Python version
        if sys.version_info < (3, 8):
            print_error(f"Python 3.8+ required, found {sys.version_info.major}.{sys.version_info.minor}")
            return False
        print_success(f"Python {sys.version_info.major}.{sys.version_info.minor} ✓")

        # Check for venv module
        try:
            import venv  # noqa: F401
            print_success("venv module available")
        except ImportError:
            if os.name == "posix":
                print_warning("venv module not available, attempting to install...")
                try:
                    subprocess.run(
                        ["sudo", "apt", "install", "-y", "python3-venv"],
                        check=True,
                        capture_output=True
                    )
                    print_success("python3-venv installed successfully")
                except subprocess.CalledProcessError:
                    print_error("Failed to install python3-venv")
                    return False
            else:
                print_error("venv module not available and cannot auto-install on this platform")
                return False

        return True

    def setup_virtual_environment(self) -> bool:
        """Setup virtual environment"""
        print_info("📦 Setting up virtual environment...")

        # Create venv if not exists
        if not self.venv_dir.exists():
            print_info(f"Creating virtual environment at {self.venv_dir} ...")
            try:
                subprocess.run([sys.executable, "-m", "venv", str(self.venv_dir)],
                               check=True, capture_output=True)
                print_success("Virtual environment created")
            except subprocess.CalledProcessError as e:
                print_error(f"Failed to create virtual environment: {e}")
                return False
        else:
            print_success(f"Virtual environment already exists at {self.venv_dir}")

        # Verify executables exist
        if not self.python_exec.exists() or not self.pip_exec.exists():
            print_error("Virtual environment seems corrupted (python/pip not found)")
            return False

        return True

    def install_packages(self, force_reinstall: bool = False) -> bool:
        """Install required packages"""

        print_info("📦 Checking and installing packages...")
        try:
            subprocess.run([str(self.pip_exec), "install", "--upgrade", "pip"],
                           check=True, capture_output=True)
            print_success("pip upgraded")
        except subprocess.CalledProcessError:
            print_warning("Could not upgrade pip")

        # Check and install required packages
        missing_packages = []
        for package in self.required_packages:
            if force_reinstall:
                missing_packages.append(package)
            else:
                try:
                    result = subprocess.run(
                        [str(self.pip_exec), "show", package],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        missing_packages.append(package)
                except Exception:
                    missing_packages.append(package)

        if missing_packages:
            print_info(f"Installing {len(missing_packages)} packages: {', '.join(missing_packages)}")

            for package in missing_packages:
                try:
                    print_info(f"➕ Installing {package}...")
                    subprocess.run(
                        [str(self.pip_exec), "install", package],
                        check=True,
                        capture_output=True
                    )
                    print_success(f"✅ {package} installed")
                except subprocess.CalledProcessError as e:
                    print_error(f"❌ Failed to install {package}: {e}")
                    return False

            print_success("All required packages installed successfully!")
        else:
            print_success("All required packages are already installed")

        # Install optional packages (don't fail if they don't install)
        print_info("Installing optional packages...")
        for package in self.optional_packages:
            try:
                result = subprocess.run(
                    [str(self.pip_exec), "show", package],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    subprocess.run(
                        [str(self.pip_exec), "install", package],
                        check=True,
                        capture_output=True
                    )
                    print_success(f"✅ Optional package {package} installed")
            except Exception:
                print_warning(f"⚠️ Optional package {package} failed to install (not critical)")

        return True

    def validate_blackbot_installation(self) -> bool:
        """Validate BlackBoT installation and configuration"""
        print_info("🔍 Validating BlackBoT installation...")

        # Check for BlackBoT.py
        if not self.has_blackbot:
            print_error("BlackBoT.py not found!")
            return False
        print_success("BlackBoT.py found")

        # Check if unified manager is available
        if not self.has_unified_manager:
            print_warning("Unified manager not found - will use basic direct mode")
        else:
            print_success("Unified manager available")

        # Check configuration files
        config_files = [".env", "settings.py", "instances.json"]
        config_found = any((self.base_dir / f).exists() for f in config_files)

        if config_found:
            print_success("Configuration files found")
        else:
            print_warning("No configuration files found - will need setup")

        # Test import of critical modules
        try:
            result = subprocess.run(
                [
                    str(self.python_exec),
                    "-c",
                    "import twisted, psutil, bcrypt; print('Core modules OK')"
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                print_success("Core modules import successfully")
            else:
                print_error(f"Module import failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print_error("Module import test timed out")
            return False
        except Exception as e:
            print_error(f"Module import test failed: {e}")
            return False

        return True

    def _venv_path_in_env(self, env: dict) -> dict:
        """Helper: injectează calea spre venv în PATH, cross-platform."""
        old_path = env.get("PATH", "")
        new_path = f"{self.venv_bin}{os.pathsep}{old_path}"
        env["PATH"] = new_path
        env["VIRTUAL_ENV"] = str(self.venv_dir)
        return env

    # -------------------------------------------------------------------------
    # MANAGER / INSTANCES
    # -------------------------------------------------------------------------
    def launch_unified_manager(self, args: List[str] = None) -> bool:
        """Launch unified manager in virtual environment"""
        if not self.has_unified_manager:
            print_error("Unified manager not available")
            return False

        print_info("🚀 Launching Unified BlackBoT Manager...")

        try:
            cmd = [str(self.python_exec), "Manager.py"]
            if args:
                cmd.extend(args)

            env = os.environ.copy()
            env = self._venv_path_in_env(env)

            result = subprocess.run(cmd, env=env, cwd=self.base_dir)
            return result.returncode == 0

        except Exception as e:
            print_error(f"Failed to launch unified manager: {e}")
            return False

    def start_instance_with_dependencies(self, instance_name: str, background: bool = True) -> bool:
        """Start instance ensuring all dependencies are installed"""
        print_info(f"🚀 Starting instance '{instance_name}' with dependency check...")

        # Load instance configuration if unified manager is available
        instance_config = None
        if self.has_unified_manager:
            try:
                registry_file = self.base_dir / "instances.json"
                if registry_file.exists():
                    with open(registry_file, 'r', encoding='utf-8') as f:
                        registry = json.load(f)

                    instances = registry.get('instances', {})
                    if instance_name in instances:
                        instance_config = instances[instance_name]
                        print_success(f"Loaded configuration for instance '{instance_name}'")
            except Exception as e:
                print_warning(f"Could not load instance config: {e}")

        # Prepare environment
        env = os.environ.copy()
        env['BLACKBOT_INSTANCE_NAME'] = instance_name
        env = self._venv_path_in_env(env)

        # Load instance-specific environment if available
        if instance_config and 'config_file' in instance_config:
            env_file = self.base_dir / instance_config['config_file']
            if env_file.exists():
                print_info(f"Loading environment from {instance_config['config_file']}")
                try:
                    with open(env_file, 'r', encoding='utf-8', errors='replace') as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith('#') and '=' in line:
                                key, value = line.split('=', 1)
                                env[key.strip()] = value.strip().strip('"\'')
                except Exception as e:
                    print_warning(f"Error loading .env file: {e}")

        # Choose startup method
        startup_options = ["BlackBoT.py"]
        startup_file = None

        for option in startup_options:
            if (self.base_dir / option).exists():
                startup_file = option
                break

        if not startup_file:
            print_error("No startup file found (BlackBoT.py)")
            return False

        try:
            cmd = [str(self.python_exec), startup_file]

            if background:
                # Create log directory
                if instance_config and 'log_file' in instance_config:
                    log_file = self.base_dir / instance_config['log_file']
                    log_file.parent.mkdir(parents=True, exist_ok=True)
                else:
                    log_file = self.base_dir / "logs" / f"{instance_name}.log"
                    log_file.parent.mkdir(parents=True, exist_ok=True)

                print_info(f"Starting in background, logging to {log_file}")

                # pe Windows folosim pythonw dacă există
                python_exec = sys.executable
                if os.name == "nt" and python_exec.lower().endswith("python.exe"):
                    candidate = python_exec[:-len("python.exe")] + "pythonw.exe"
                    if os.path.exists(candidate):
                        python_exec = candidate
                cmd_bg = [python_exec] + cmd[1:]

                if os.name == "nt":
                    creationflags = 0
                    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
                    creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

                    with open(log_file, 'a', encoding='utf-8', errors='replace') as f:
                        process = subprocess.Popen(
                            cmd_bg,
                            cwd=self.base_dir,
                            env=env,
                            stdout=f,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            creationflags=creationflags,
                            close_fds=True,
                        )
                else:
                    with open(log_file, 'a', encoding='utf-8', errors='replace') as f:
                        process = subprocess.Popen(
                            cmd_bg,
                            cwd=self.base_dir,
                            env=env,
                            stdout=f,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            start_new_session=True,
                            close_fds=True,
                        )

                # Save PID (fallback-only; Manager gestionează PID-urile sale)
                if instance_config and 'pid_file' in instance_config:
                    pid_file = self.base_dir / instance_config['pid_file']
                else:
                    pid_file = self.base_dir / f"{instance_name}.pid"

                pid_file.parent.mkdir(parents=True, exist_ok=True)
                with open(pid_file, 'w', encoding='utf-8', errors='replace') as f:
                    f.write(str(process.pid))

                print_success(f"Instance '{instance_name}' started (PID: {process.pid})")
                print_info(f"📋 Log file: {log_file}")

                if os.name == "nt":
                    from pathlib import Path as _P
                    py_cmd = _P(sys.executable).name or "python"
                    this_script = _P(sys.argv[0]).name
                    print_info(f"🛑 Stop with: {py_cmd} {this_script} stop {instance_name}")
                else:
                    print_info(f"🛑 Stop with: kill {process.pid}")

                return True
            else:
                # Foreground execution
                print_info(f"Starting instance '{instance_name}' in foreground...")
                result = subprocess.run(cmd, env=env, cwd=self.base_dir)
                return result.returncode == 0

        except Exception as e:
            print_error(f"Failed to start instance '{instance_name}': {e!r}")
            return False

    # -------------------------------------------------------------------------
    # MENIU INTERACTIV
    # -------------------------------------------------------------------------
    def show_main_menu(self):
        """Show main hybrid launcher menu"""
        while True:
            print_header("🔥 Hybrid BlackBoT Launcher")

            print(f"{Colors.BOLD}System Status:{Colors.END}")
            print(f"   📁 Directory: {self.base_dir}")
            print(f"   🐍 Virtual Env: {'✅' if self.venv_dir.exists() else '❌'} ({self.venv_dir})")
            print(f"   🤖 Unified Manager: {'✅' if self.has_unified_manager else '❌'}")
            print(f"   📄 BlackBoT.py: {'✅' if self.has_blackbot else '❌'}")
            print()

            print(f"{Colors.BOLD}Available Options:{Colors.END}")
            print("  1. 🔧 Setup Environment (dependencies, venv, packages)")
            print("  2. 🎛️  Unified Manager (configuration, multi-instance)")
            print("  3. 🔍 Validate Installation (check everything)")
            print("  4. 📦 Force Reinstall Packages")
            print("  5. 🎯 Start Specific Instance (with dep check)")
            print("  6. 📊 System Information")
            print("  7. ❌ Exit")

            try:
                choice = input(f"\n{Colors.CYAN}Choose option [1-8]: {Colors.END}").strip()

                if choice == "1":
                    self.setup_complete_environment()
                elif choice == "2":
                    if self.has_unified_manager:
                        if not self.venv_dir.exists():
                            print_warning("Virtual environment not set up!")
                            if input(f"{Colors.CYAN}Set up environment first? [Y/n]: {Colors.END}").lower() != 'n':
                                self.setup_complete_environment()
                        self.launch_unified_manager()
                    else:
                        print_error("Unified manager not available")
                elif choice == "3":
                    self.validate_complete_installation()
                elif choice == "4":
                    if self.venv_dir.exists():
                        self.install_packages(force_reinstall=True)
                    else:
                        print_error("Virtual environment not set up")
                elif choice == "5":
                    self.start_instance_interactive()
                elif choice == "6":
                    self.show_system_information()
                elif choice == "7":
                    print_info("Goodbye!")
                    break
                else:
                    print_warning("Invalid choice")

            except KeyboardInterrupt:
                print(f"\n{Colors.WARNING}Exiting...{Colors.END}")
                break
            except Exception as e:
                print_error(f"Error: {e}")

            if choice != "8":
                input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")

    # -------------------------------------------------------------------------
    # HIGH-LEVEL OPS
    # -------------------------------------------------------------------------
    def setup_complete_environment(self) -> bool:
        """Complete environment setup combining all checks"""
        print_header("🔧 Complete Environment Setup")

        print_info("Starting comprehensive environment setup...")
        print_info("This will ensure all dependencies, virtual environment, and packages are ready")
        print()

        # Step 1: Check Python
        if not self.check_python_environment():
            print_error("Python environment check failed")
            return False

        # Step 2: Setup virtual environment
        if not self.setup_virtual_environment():
            print_error("Virtual environment setup failed")
            return False

        # Step 3: Install packages
        if not self.install_packages():
            print_error("Package installation failed")
            return False

        # Step 4: Validate installation
        if not self.validate_blackbot_installation():
            print_error("BlackBoT validation failed")
            return False

        print_success("🎉 Complete environment setup finished successfully!")
        print_info("You can now use any launch method with confidence")
        return True

    def validate_complete_installation(self) -> bool:
        """Comprehensive installation validation"""
        print_header("🔍 Complete Installation Validation")

        all_good = True

        print_info("Checking Python environment...")
        if self.check_python_environment():
            print_success("✅ Python environment OK")
        else:
            print_error("❌ Python environment issues")
            all_good = False

        print_info("Checking virtual environment...")
        if self.venv_dir.exists() and self.python_exec.exists():
            print_success("✅ Virtual environment OK")
        else:
            print_error("❌ Virtual environment issues")
            all_good = False

        print_info("Checking packages...")
        missing = []
        for package in self.required_packages:
            try:
                result = subprocess.run(
                    [str(self.pip_exec), "show", package],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    missing.append(package)
            except Exception:
                missing.append(package)

        if missing:
            print_error(f"❌ Missing packages: {', '.join(missing)}")
            all_good = False
        else:
            print_success("✅ All packages installed")

        print_info("Checking BlackBoT installation...")
        if self.validate_blackbot_installation():
            print_success("✅ BlackBoT installation OK")
        else:
            print_error("❌ BlackBoT installation issues")
            all_good = False

        print_info("Checking configuration...")
        config_files = ["instances.json"]
        configs_found = [(f, (self.base_dir / f).exists()) for f in config_files]

        for config_file, exists in configs_found:
            if exists:
                print_success(f"✅ {config_file} found")
            else:
                print_warning(f"⚠️ {config_file} not found")

        print()
        if all_good:
            print_success("🎉 All validation checks passed!")
            print_info("Your BlackBoT installation is ready for production")
        else:
            print_warning("⚠️ Some validation checks failed")
            print_info("Run 'Setup Environment' to fix issues")

        return all_good

    def start_instance_interactive(self):
        """Interactive instance starting"""
        print_header("🎯 Start Specific Instance")

        instances = []
        if self.has_unified_manager and (self.base_dir / "instances.json").exists():
            try:
                with open(self.base_dir / "instances.json", 'r', encoding='utf-8') as f:
                    registry = json.load(f)
                instances = list(registry.get('instances', {}).keys())
            except Exception:
                pass

        if instances:
            print_info("Available instances:")
            for i, instance in enumerate(instances, 1):
                print(f"  {i}. {instance}")

            try:
                choice = int(input(f"{Colors.CYAN}Choose instance [1-{len(instances)}]: {Colors.END}"))
                if 1 <= choice <= len(instances):
                    instance_name = instances[choice - 1]
                else:
                    print_error("Invalid choice")
                    return
            except (ValueError, KeyboardInterrupt):
                print_warning("Operation cancelled")
                return
        else:
            instance_name = input(f"{Colors.CYAN}Instance name [main]: {Colors.END}").strip()
            if not instance_name:
                instance_name = "main"

        background = input(f"{Colors.CYAN}Start in background? [Y/n]: {Colors.END}").lower() != 'n'

        self.start_instance_with_dependencies(instance_name, background)

    def show_system_information(self):
        """Show detailed system information"""
        print_header("📊 System Information")

        print(f"{Colors.BOLD}Environment:{Colors.END}")
        print(f"  📁 Base Directory: {self.base_dir}")
        print(
            f"  🐍 Python: {sys.executable} (v{sys.version_info.major}."
            f"{sys.version_info.minor}.{sys.version_info.micro})"
        )
        print(
            f"  🎯 Virtual Env: {self.venv_dir} "
            f"({'exists' if self.venv_dir.exists() else 'missing'})"
        )
        print()

        print(f"{Colors.BOLD}Available Components:{Colors.END}")
        print(f"  📄 BlackBoT.py: {'✅' if self.has_blackbot else '❌'}")
        print(f"  🤖 Unified Manager: {'✅' if self.has_unified_manager else '❌'}")
        print()

        print(f"{Colors.BOLD}Configuration Files:{Colors.END}")
        config_files = ["instances.json"]
        for config_file in config_files:
            exists = (self.base_dir / config_file).exists()
            print(f"  📄 {config_file}: {'✅' if exists else '❌'}")

        print()

        print(f"{Colors.BOLD}Package Status:{Colors.END}")
        if self.venv_dir.exists():
            for package in self.required_packages:
                try:
                    result = subprocess.run(
                        [str(self.pip_exec), "show", package],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        lines = result.stdout.split('\n')
                        version = "unknown"
                        for line in lines:
                            if line.startswith("Version:"):
                                version = line.split(":", 1)[1].strip()
                                break
                        print(f"  📦 {package}: ✅ v{version}")
                    else:
                        print(f"  📦 {package}: ❌")
                except Exception:
                    print(f"  📦 {package}: ❌")
        else:
            print("  ⚠️ Virtual environment not set up")


def main():
    """Main entry point"""
    if not Path("BlackBoT.py").exists():
        print_error("BlackBoT.py not found!")
        print_error("Please run this script from the BlackBoT directory")
        return False

    launcher = HybridBlackBotLauncher()

    if len(sys.argv) > 1:
        command = sys.argv[1].lower()

        if command == "setup":
            return launcher.setup_complete_environment()
        elif command == "validate":
            return launcher.validate_complete_installation()
        elif command == "manager":
            return launcher.launch_unified_manager(sys.argv[2:] if len(sys.argv) > 2 else None)
        elif command == "start":
            instance = sys.argv[2] if len(sys.argv) > 2 else "main"
            return launcher.start_instance_with_dependencies(instance)
        elif command == "smart":
            launcher.smart_start()
            return True
        elif command in ["help", "--help", "-h"]:
            print(f"""Hybrid BlackBoT Launcher

Usage: {sys.argv[0]} [command] [options]

Commands:
  setup              Set up complete environment (venv, packages, validation)
  validate           Validate complete installation
  manager [args]     Launch unified manager with optional arguments
  start [instance]   Start specific instance with dependency checking
  smart              Auto-detect and use best launch method
  help               Show this help

Examples:
  {sys.argv[0]} setup                # Complete environment setup
  {sys.argv[0]} manager list         # Launch manager and list instances
  {sys.argv[0]} start gamebot        # Start gamebot instance with dep check
  {sys.argv[0]} smart                # Auto-detect best method

Interactive mode: {sys.argv[0]} (no arguments)
""")
        else:
            print_error(f"Unknown command: {command}")
            print_info(f"Use '{sys.argv[0]} help' for available commands")
    else:
        launcher.show_main_menu()

    return True


if __name__ == "__main__":
    main()
