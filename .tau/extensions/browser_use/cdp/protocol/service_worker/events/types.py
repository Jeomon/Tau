"""CDP ServiceWorker Events"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..types import ServiceWorkerErrorMessage
    from ..types import ServiceWorkerRegistration
    from ..types import ServiceWorkerVersion

class workerErrorReportedEvent(TypedDict, total=True):
    errorMessage: ServiceWorkerErrorMessage
class workerRegistrationUpdatedEvent(TypedDict, total=True):
    registrations: List[ServiceWorkerRegistration]
class workerVersionUpdatedEvent(TypedDict, total=True):
    versions: List[ServiceWorkerVersion]