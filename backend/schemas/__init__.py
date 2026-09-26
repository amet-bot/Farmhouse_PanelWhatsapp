from schemas.auth import LoginRequest, TokenResponse  # noqa: F401 — re-exportaciones del paquete
from schemas.branch import BranchCreate, BranchResponse  # noqa: F401
from schemas.user import UserCreate, UserUpdate, UserResponse  # noqa: F401
from schemas.device import DeviceCreate, DeviceUpdate, DeviceResponse  # noqa: F401
from schemas.contact import ContactCreate, ContactUpdate, ContactResponse  # noqa: F401
from schemas.message import MessageCreate, MessageResponse  # noqa: F401
from schemas.order import OrderCreate, OrderUpdate, OrderResponse  # noqa: F401
from schemas.conversation import ConversationCreate, ConversationTransferRequest, ConversationResponse  # noqa: F401

TokenResponse.model_rebuild()