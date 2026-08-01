"""CDP Ads Domain"""
from __future__ import annotations
from typing import TYPE_CHECKING
from .methods.service import AdsMethods
from .events.service import AdsEvents

if TYPE_CHECKING:
    from ...service import Client

class Ads(AdsMethods, AdsEvents):
    """
    A domain for ad-related metrics and data.
    """
    def __init__(self, client: Client):
        """
        Initialize the Ads domain.
        
        Args:
            client (Client): The parent CDP client instance.
        """
        AdsMethods.__init__(self, client)
        AdsEvents.__init__(self, client)