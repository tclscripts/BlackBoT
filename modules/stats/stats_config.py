"""
IRC Statistics - Configuration
===============================
Configurare centralizată pentru stats system.
"""

import os
import json
from pathlib import Path
from core.log import get_logger

logger = get_logger("stats_config")


class StatsConfig:
    """Configurare pentru stats system"""
    
    # Default values
    DEFAULTS = {
        'enabled': True,
        'api_enabled': True,
        'api_port': 8000,
        'api_host': '0.0.0.0',
        'aggregator_enabled': True,
        'aggregator_interval': 300,  # 5 minutes
        'batch_size': 50,
        'flush_interval': 5.0,  # seconds
        'archive_days': 180,  # Keep events for 6 months
        'max_events_per_aggregation': 5000,
    }
    
    def __init__(self, config_file='stats_config.json'):
        self.config_file = config_file
        self.config = self.DEFAULTS.copy()
        self.load_config()
    
    def load_config(self):
        """Încarcă configurare din fișier sau environment variables"""
        
        # 1. Try to load from JSON file
        config_path = Path(self.config_file)
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    file_config = json.load(f)
                
                self.config.update(file_config)
                logger.info(f"Loaded config from {self.config_file}")
            
            except Exception as e:
                logger.error(f"Failed to load config from {self.config_file}: {e}")
        
        # 2. Override with environment variables (higher priority)
        env_mappings = {
            'STATS_ENABLED': ('enabled', lambda x: x.lower() == 'true'),
            'STATS_API_ENABLED': ('api_enabled', lambda x: x.lower() == 'true'),
            'STATS_API_PORT': ('api_port', int),
            'STATS_API_HOST': ('api_host', str),
            'STATS_AGGREGATOR_ENABLED': ('aggregator_enabled', lambda x: x.lower() == 'true'),
            'STATS_AGGREGATOR_INTERVAL': ('aggregator_interval', int),
            'STATS_BATCH_SIZE': ('batch_size', int),
            'STATS_FLUSH_INTERVAL': ('flush_interval', float),
        }
        
        for env_var, (config_key, converter) in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                try:
                    self.config[config_key] = converter(value)
                    logger.info(f"Config override from env: {config_key} = {self.config[config_key]}")
                except Exception as e:
                    logger.error(f"Failed to parse env var {env_var}: {e}")
        
        logger.info(f"Stats config loaded: {self.config}")
    
    def save_config(self):
        """Salvează configurarea în fișier"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
            
            logger.info(f"Config saved to {self.config_file}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False
    
    def get(self, key, default=None):
        """Get config value"""
        return self.config.get(key, default)
    
    def set(self, key, value):
        """Set config value"""
        self.config[key] = value
    
    def is_enabled(self):
        """Check if stats system is enabled"""
        return self.config.get('enabled', True)
    
    def is_api_enabled(self):
        """Check if API server is enabled"""
        return self.config.get('api_enabled', True) and self.is_enabled()
    
    def is_aggregator_enabled(self):
        """Check if aggregator is enabled"""
        return self.config.get('aggregator_enabled', True) and self.is_enabled()
    
    def get_api_port(self):
        """Get API port"""
        return self.config.get('api_port', 8000)
    
    def get_api_host(self):
        """Get API host"""
        return self.config.get('api_host', '0.0.0.0')
    
    def get_aggregator_interval(self):
        """Get aggregator interval in seconds"""
        return self.config.get('aggregator_interval', 300)
    
    def get_batch_size(self):
        """Get event batch size"""
        return self.config.get('batch_size', 50)
    
    def get_flush_interval(self):
        """Get flush interval in seconds"""
        return self.config.get('flush_interval', 5.0)


# Global config instance
_config = None

def get_stats_config():
    """Get global stats config instance"""
    global _config
    if _config is None:
        _config = StatsConfig()
    return _config


def create_default_config_file():
    """Creează fișier de configurare cu defaults"""
    config = StatsConfig()
    
    # Add comments as a separate dict (not in JSON)
    config_with_comments = {
        '_comments': {
            'enabled': 'Master switch pentru stats system',
            'api_enabled': 'Enable/disable web API server',
            'api_port': 'Port pentru API server (default: 8000)',
            'api_host': 'Host pentru API server (0.0.0.0 = toate interfețele)',
            'aggregator_enabled': 'Enable/disable periodic aggregator',
            'aggregator_interval': 'Interval agregare în secunde (300 = 5 min)',
            'batch_size': 'Număr de evenimente per batch',
            'flush_interval': 'Interval flush în secunde',
            'archive_days': 'Păstrează evenimente X zile (cleanup automat)',
        },
        **config.DEFAULTS
    }
    
    try:
        with open('stats_config.json', 'w') as f:
            json.dump(config_with_comments, f, indent=4)
        
        print("✓ Created stats_config.json with default values")
        return True
    
    except Exception as e:
        print(f"✗ Failed to create config file: {e}")
        return False


if __name__ == "__main__":
    # Test: create default config
    print("Creating default stats_config.json...")
    create_default_config_file()
    
    # Test: load config
    print("\nLoading config...")
    config = get_stats_config()
    print(f"Stats enabled: {config.is_enabled()}")
    print(f"API enabled: {config.is_api_enabled()}")
    print(f"API port: {config.get_api_port()}")
    print(f"Aggregator interval: {config.get_aggregator_interval()}s")
