"""
IRC Statistics - Dependency Checker
====================================
Verifică dependencies necesare pentru stats system.
Instalarea se face de către Launcher/Manager.
"""

import importlib.util
from core.log import get_logger

logger = get_logger("stats_checker")


class StatsDependencyChecker:
    """Verifică dependencies pentru stats system"""
    
    REQUIRED_PACKAGES = {
        'fastapi': 'fastapi',
        'uvicorn': 'uvicorn',
        'pydantic': 'pydantic',
    }
    
    @classmethod
    def check_package(cls, package_name):
        """Verifică dacă un package e instalat"""
        spec = importlib.util.find_spec(package_name)
        return spec is not None
    
    @classmethod
    def check_all_dependencies(cls):
        """
        Verifică toate dependencies necesare.
        Returns: (success: bool, missing: list)
        """
        missing = []
        
        logger.debug("Checking stats system dependencies...")
        
        for import_name, package_name in cls.REQUIRED_PACKAGES.items():
            if not cls.check_package(import_name):
                logger.warning(f"Missing dependency: {package_name}")
                missing.append(package_name)
            else:
                logger.debug(f"✓ {package_name} available")
        
        if missing:
            logger.warning(f"Missing dependencies: {', '.join(missing)}")
            logger.info("These packages should be installed by Launcher/Manager")
            return False, missing
        else:
            logger.info("✓ All stats dependencies available")
            return True, []
    
    @classmethod
    def verify_installation(cls):
        """Verifică că toate package-urile pot fi importate"""
        try:
            import fastapi
            import uvicorn
            import pydantic
            
            logger.info("✓ All stats dependencies verified")
            return True
        
        except ImportError as e:
            logger.error(f"✗ Dependency verification failed: {e}")
            logger.error("Make sure fastapi, uvicorn, pydantic are installed")
            logger.error("Launcher/Manager should handle package installation")
            return False


def check_stats_dependencies():
    """
    Wrapper function pentru a fi apelat din stats_manager.
    Returns: bool - True dacă toate dependencies sunt OK
    """
    try:
        checker = StatsDependencyChecker()
        success, missing = checker.check_all_dependencies()
        
        if success:
            # Verify prin import
            success = checker.verify_installation()
        
        return success
    
    except Exception as e:
        logger.error(f"Error during dependency check: {e}", exc_info=True)
        return False


# Quick test
if __name__ == "__main__":
    print("Testing Stats Auto-Installer...")
    success = auto_install_stats_dependencies()
    print(f"Result: {'SUCCESS' if success else 'FAILED'}")
