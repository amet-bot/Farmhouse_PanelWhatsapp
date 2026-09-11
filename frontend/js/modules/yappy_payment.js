(async function () {
  "use strict";

  const params = new URLSearchParams(window.location.search);
  const orderCode = params.get("order") || "";
  const paymentToken = params.get("token") || "";
  const isDemo = params.get("demo") === "1";
  const statusBox = document.getElementById("paymentStatus");
  const buttonWrap = document.getElementById("yappyButtonWrap");
  const phoneField = document.getElementById("phoneField");
  const phoneInput = document.getElementById("yappyPhone");
  const demoBanner = document.getElementById("demoBanner");
  const demoButton = document.getElementById("demoPayButton");

  function normalizedPhoneDigits(value) {
    let digits = String(value || "").replace(/\D/g, "");
    if (digits.startsWith("507") && digits.length === 11) digits = digits.slice(3);
    return digits;
  }

  function setStatus(message, type = "") {
    statusBox.textContent = message;
    statusBox.className = `payment-status${type ? ` ${type}` : ""}`;
  }

  async function responseJson(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "No pudimos comunicarnos con Yappy.");
    return data;
  }

  function runDemoFlow(order) {
    demoBanner.hidden = false;
    if (order.contact_phone) phoneInput.value = order.contact_phone;
    phoneField.hidden = false;
    demoButton.hidden = false;
    setStatus("Simulación: confirma tu número y toca el botón para ver cómo se vería el pago real.");

    demoButton.addEventListener("click", () => {
      const phoneDigits = normalizedPhoneDigits(phoneInput.value);
      if (phoneDigits.length !== 8) {
        setStatus("Escribe un número de Yappy válido (8 dígitos).", "error");
        phoneInput.focus();
        return;
      }
      demoButton.disabled = true;
      setStatus("Simulación: creando tu solicitud segura en Yappy…");
      setTimeout(() => {
        setStatus("✅ Simulación exitosa. Así se vería para tu cliente — actívalo con tus credenciales de Yappy Comercial para que sea un cobro real.", "success");
        demoButton.disabled = false;
      }, 1400);
    });
  }

  if (!orderCode || !paymentToken) {
    setStatus("Este enlace de pago está incompleto. Solicita uno nuevo por WhatsApp.", "error");
    return;
  }

  try {
    const [order, config] = await Promise.all([
      fetch(`/api/payments/yappy/orders/${encodeURIComponent(orderCode)}?token=${encodeURIComponent(paymentToken)}`).then(responseJson),
      fetch("/api/payments/yappy/config").then(responseJson),
    ]);
    document.getElementById("orderCode").textContent = order.order_code;
    document.getElementById("orderTotal").textContent = `$${order.total}`;

    if (order.payment_status === "paid") {
      setStatus("Este pedido ya fue pagado correctamente. ¡Gracias!", "success");
      return;
    }
    if (!order.configured || !config.enabled) {
      if (isDemo) return runDemoFlow(order);
      setStatus("El pago automático por Yappy todavía está pendiente de activación comercial. Escríbenos por WhatsApp para ayudarte.", "error");
      return;
    }

    if (order.contact_phone) phoneInput.value = order.contact_phone;
    phoneField.hidden = false;

    const script = document.createElement("script");
    script.type = "module";
    script.src = config.button_cdn_url;
    script.onload = () => {
      const button = document.querySelector("btn-yappy");
      buttonWrap.hidden = false;
      setStatus("Confirma tu número y toca el botón para enviar la solicitud a tu Yappy.");

      button.addEventListener("eventClick", async () => {
        const phoneDigits = normalizedPhoneDigits(phoneInput.value);
        if (phoneDigits.length !== 8) {
          setStatus("Escribe un número de Yappy válido (8 dígitos).", "error");
          phoneInput.focus();
          return;
        }
        button.isButtonLoading = true;
        setStatus("Creando tu solicitud segura en Yappy…");
        try {
          const session = await fetch("/api/payments/yappy/session", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
            body: JSON.stringify({ order_code: orderCode, token: paymentToken, phone: phoneDigits }),
          }).then(responseJson);
          button.eventPayment(session);
          setStatus("Solicitud enviada. Revisa la sección Pendientes en tu aplicación Yappy.");
        } catch (error) {
          button.isButtonLoading = false;
          setStatus(error.message, "error");
        }
      });
      button.addEventListener("eventSuccess", () => {
        button.isButtonLoading = false;
        setStatus("¡Pago aprobado! Ya estamos confirmando tu pedido con Farmhouse.", "success");
      });
      button.addEventListener("eventError", () => {
        button.isButtonLoading = false;
        setStatus("El pago no pudo completarse. Puedes intentarlo nuevamente.", "error");
      });
    };
    script.onerror = () => setStatus("No pudimos cargar el botón de Yappy. Intenta nuevamente en unos minutos.", "error");
    document.head.appendChild(script);
  } catch (error) {
    setStatus(error.message, "error");
  }
})();
