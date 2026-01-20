"""
IRC Statistics System
=====================
Modern statistics system for BlackBoT.

Usage:
    from modules.stats import init_stats_system, shutdown_stats_system
    
    # In BlackBoT signedOn():
    init_stats_system(self)
    
    # In BlackBoT connectionLost():
    shutdown_stats_system()
"""

__version__ = "1.0.0"

# Import main functions pentru easy access
from modules.stats.stats_manager import (
    init_stats_system,
    shutdown_stats_system,
    get_stats_status,
    get_stats_manager
)

__all__ = [
    'init_stats_system',
    'shutdown_stats_system',
    'get_stats_status',
    'get_stats_manager',
]
