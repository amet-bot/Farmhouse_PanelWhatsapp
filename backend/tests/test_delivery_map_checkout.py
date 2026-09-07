from decimal import Decimal

from models.contact import Contact
from models.order import Order
from services.delivery_geo import distance_km, fee_for_distance, is_in_panama_city


def _delivery_payload(**overrides):
    payload = {
        "branch_code": "CLY",
        "delivery_type": "delivery",
        "delivery_address": "Ciudad del Saber, Panamá",
        "delivery_building": "Edificio 100",
        "delivery_unit": "Piso 2",
        "delivery_reference": "Recepción principal",
        "delivery_latitude": 9.005,
        "delivery_longitude": -79.565,
        "payment_method": "yappy",
        "fulfillment_type": "asap",
        "customer_name": "Cliente Mapa",
        "customer_phone": "6555-0101",
        "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}],
    }
    payload.update(overrides)
    return payload


def test_delivery_fee_ranges_are_deterministic():
    assert fee_for_distance(Decimal("1.99")) == Decimal("5.00")
    assert fee_for_distance(Decimal("2.00")) == Decimal("10.00")
    assert fee_for_distance(Decimal("5.00")) == Decimal("10.00")
    assert fee_for_distance(Decimal("5.01")) == Decimal("15.00")
    assert distance_km(9.003859, -79.573043, 9.005, -79.565) < Decimal("2.00")


def test_panama_city_geofence_rejects_points_outside_city(client, clayton_branch):
    assert is_in_panama_city(9.005, -79.565)
    assert not is_in_panama_city(8.60, -79.90)

    response = client.post(
        "/api/orders/public",
        json=_delivery_payload(delivery_latitude=8.60, delivery_longitude=-79.90),
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 422
    assert "fuera de Ciudad de Panamá" in response.json()["detail"]


def test_delivery_saves_general_customer_address_and_schedule(client, clayton_branch, db_session):
    response = client.post(
        "/api/orders/public",
        json=_delivery_payload(fulfillment_type="scheduled", scheduled_for="2099-01-15T18:30:00-05:00"),
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["delivery_cost"] == "5.00"

    order = db_session.query(Order).filter(Order.order_code == response.json()["order_code"]).one()
    contact = db_session.query(Contact).filter(Contact.id == order.conversation.customer_id).one()
    assert order.fulfillment_type == "scheduled"
    assert order.scheduled_for is not None
    assert str(order.delivery_distance_km) != "0.00"
    assert contact.address == "Ciudad del Saber, Panamá"
    assert contact.building_or_house == "Edificio 100"
    assert contact.floor_or_unit == "Piso 2"
    assert contact.address_reference == "Recepción principal"


def test_cash_is_no_longer_accepted(client, clayton_branch):
    response = client.post(
        "/api/orders/public",
        json=_delivery_payload(payment_method="cash"),
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 422
