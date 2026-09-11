"""Integración server-side con el Botón de Pago Yappy V2."""

import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from jose import JWTError, jwt

from config import settings

YAPPY_PAYMENT_TOKEN_TYPE = "yappy_payment"


class YappyConfigurationError(RuntimeError):
    pass


class YappyGatewayError(RuntimeError):
    pass


def yappy_domain() -> str:
    return str(settings.YAPPY_DOMAIN or settings.PUBLIC_BASE_URL).rstrip("/")


def is_yappy_configured() -> bool:
    return bool(
        settings.YAPPY_ENABLED
        and str(settings.YAPPY_MERCHANT_ID or "").strip()
        and str(settings.YAPPY_SECRET_KEY or "").strip()
        and yappy_domain().startswith("https://")
    )


def create_yappy_payment_token(order_code: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "typ": YAPPY_PAYMENT_TOKEN_TYPE,
            "order": order_code,
            "iat": now,
            "exp": now + timedelta(hours=24),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_yappy_payment_token(token: str, order_code: str) -> bool:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        return False
    return (
        payload.get("typ") == YAPPY_PAYMENT_TOKEN_TYPE
        and payload.get("order") == order_code
    )


def build_yappy_payment_url(order_code: str) -> str:
    token = create_yappy_payment_token(order_code)
    return f"{str(settings.PUBLIC_BASE_URL).rstrip('/')}/pago-yappy?order={order_code}&token={token}"


def local_yappy_alias(phone: str) -> str:
    digits = "".join(character for character in str(phone) if character.isdigit())
    if digits.startswith("507") and len(digits) == 11:
        digits = digits[3:]
    if len(digits) != 8:
        raise YappyGatewayError(
            "El teléfono debe ser un número panameño de 8 dígitos registrado en Yappy."
        )
    return digits


def local_yappy_alias_or_none(phone: str) -> str | None:
    try:
        return local_yappy_alias(phone)
    except YappyGatewayError:
        return None


def _gateway_json(response: httpx.Response, public_message: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Respuesta JSON inválida")
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise YappyGatewayError(public_message) from exc


async def create_yappy_order(*, order_code: str, phone: str, total: Decimal) -> dict[str, Any]:
    if not is_yappy_configured():
        raise YappyConfigurationError(
            "Yappy todavía no está activado para este comercio."
        )

    base_url = str(settings.YAPPY_API_BASE_URL).rstrip("/")
    merchant_id = str(settings.YAPPY_MERCHANT_ID).strip()
    domain = yappy_domain()
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            validation = await client.post(
                f"{base_url}/payments/validate/merchant",
                json={"merchantId": merchant_id, "urlDomain": domain},
            )
        except httpx.HTTPError as exc:
            raise YappyGatewayError("No pudimos conectarnos con Yappy en este momento.") from exc
        validation_data = _gateway_json(
            validation, "Yappy no pudo validar el comercio en este momento."
        )
        authorization = (validation_data.get("body") or {}).get("token")
        if not authorization:
            raise YappyGatewayError("Yappy no devolvió la autorización del comercio.")

        try:
            payment = await client.post(
                f"{base_url}/payments/payment-wc",
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                },
                json={
                    "merchantId": merchant_id,
                    "orderId": order_code[:15],
                    "domain": domain,
                    "paymentDate": int(time.time()),
                    "aliasYappy": local_yappy_alias(phone),
                    "ipnUrl": f"{domain}/api/payments/yappy/ipn",
                    "discount": "0.00",
                    "taxes": "0.00",
                    "subtotal": f"{Decimal(total):.2f}",
                    "total": f"{Decimal(total):.2f}",
                },
            )
        except httpx.HTTPError as exc:
            raise YappyGatewayError("No pudimos conectarnos con Yappy en este momento.") from exc
        data = _gateway_json(payment, "Yappy no pudo crear la solicitud de cobro.")
        body = data.get("body") or {}
        if not all(
            body.get(field) for field in ("transactionId", "token", "documentName")
        ):
            raise YappyGatewayError("La respuesta de Yappy llegó incompleta.")
        return body


def verify_yappy_ipn(
    order_id: str, payment_status: str, domain: str, received_hash: str
) -> bool:
    try:
        decoded = base64.b64decode(
            str(settings.YAPPY_SECRET_KEY or ""), validate=True
        ).decode("utf-8")
        signing_secret = decoded.split(".", 1)[0]
        expected = hmac.new(
            signing_secret.encode("utf-8"),
            f"{order_id}{payment_status}{domain}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(received_hash.lower(), expected.lower())
    except (ValueError, UnicodeDecodeError):
        return False
