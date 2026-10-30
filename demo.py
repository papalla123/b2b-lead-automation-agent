"""
demo.py: manda un lead de ejemplo al webhook de Make y muestra la respuesta.

    python demo.py hot  https://hook.make.com/xxxxxxxx
    python demo.py cold                         # usa la variable de entorno WEBHOOK_URL

Resultado esperado con las reglas de lead_logic.py:
    hot   WhatsApp, texto libre ("8 mil dólares", "esta semana")  -> HOT  (90)
    warm  Formulario, USD 3,000 y "este trimestre"                -> WARM (50)
          (trae los ocho campos: úsalo para que Make detecte la estructura del webhook)
    cold  Formulario, S/ 800 y "sin apuro"                        -> COLD (13)
"""
import json
import os
import sys
import urllib.error
import urllib.request

LEADS = {
    "hot": {
        "source": "whatsapp",
        "name": "Valeria Rojas",
        "email": "valeria.rojas@example.com",
        "phone": "whatsapp:+51 987 654 321",
        "company": "Logística Andina",
        "message": (
            "Hola, soy Valeria de Logística Andina. Queremos automatizar el seguimiento de pedidos. "
            "Tenemos 8 mil dólares aprobados y lo necesitamos funcionando esta semana."
        ),
    },
    "warm": {
        "source": "web_form",
        "name": "Diego Paredes",
        "email": "diego.paredes@example.org",
        "phone": "+51 912 345 678",
        "company": "Estudio Paredes",
        "budget": "USD 3,000",
        "urgency": "este trimestre",
        "message": "Queremos ordenar el flujo de cotizaciones. Todavía estamos comparando opciones.",
    },
    "cold": {
        "source": "web_form",
        "name": "Lucía Mendoza",
        "email": "lucia.mendoza@example.net",
        "company": "Mendoza Contadores",
        "budget": "S/ 800",
        "urgency": "sin apuro",
        "message": "Solo estoy averiguando precios para el próximo año.",
    },
}


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "hot"
    url = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("WEBHOOK_URL", "")
    if kind not in LEADS or not url:
        sys.exit(__doc__)

    lead = LEADS[kind]
    request = urllib.request.Request(
        url,
        data=json.dumps(lead).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"POST {kind}: {lead['name']} <{lead['email']}>")
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            status, body = resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:  # 4xx/5xx: también interesa ver el cuerpo
        status, body = err.code, err.read().decode("utf-8")
    try:
        body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
    except ValueError:
        pass  # sin módulo Webhook response, Make contesta "Accepted" en texto plano
    print(status, body)


if __name__ == "__main__":
    main()
