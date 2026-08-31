from schemas.auth import LoginRequest, TokenResponse, TokenPayload
from schemas.branch import BranchCreate, BranchResponse
from schemas.user import UserCreate, UserUpdate, UserResponse
from schemas.device import DeviceCreate, DeviceUpdate, DeviceResponse
from schemas.contact import ContactCreate, ContactUpdate, ContactResponse
from schemas.message import MessageCreate, MessageResponse
from schemas.order import OrderCreate, OrderUpdate, OrderResponse
from schemas.conversation import ConversationCreate, ConversationTransferRequest, ConversationResponse

from schemas.auth import TokenResponse
TokenResponse.model_rebuild()