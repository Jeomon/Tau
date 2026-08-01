"""CDP Ads Domain Methods"""
from __future__ import annotations
from ..types import *
from .types import *
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ....service import Client

class AdsMethods:
    """
    Methods for the Ads domain.
    """
    def __init__(self, client: Client):
        """
        Initialize the Ads methods.
        
        Args:
            client (Client): The parent CDP client instance.
        """
        self.client = client

    async def get_ad_metrics(self, params: getAdMetricsParameters | None = None, session_id: str | None = None) -> getAdMetricsReturns:
        """
    Retrieves ad metrics for the current page.    
        Args:
            params (getAdMetricsParameters, optional): Parameters for the getAdMetrics method.
            session_id (str, optional): Target session ID for flat protocol usage.
            
        Returns:
    getAdMetricsReturns: The result of the getAdMetrics call.
        """
        return await self.client.send(method="Ads.getAdMetrics", params=params, session_id=session_id)