"""
Tests del módulo Tier-2. Corren con:

    python -m unittest -v
    pytest -q

Los tests de la API se saltan solos si no están instaladas las dependencias.
"""
import json
import os
import unittest
from unittest import mock

import enrichment as en

GOOD = {
    "analyzed_intent": "Sales High Intent",
    "lead_value_score": 5,
    "target_department": "Sales Operations",
    "executive_summary_es": "Cliente pide cotización para 50 licencias con presupuesto aprobado.",
    "requires_immediate_alert": False,
    "suggested_next_step_es": "Enviar la propuesta hoy.",
}
CAMPOS = [
    "customer_id", "analyzed_intent", "lead_value_score", "target_department",
    "executive_summary_es", "requires_immediate_alert", "suggested_next_step_es", "enrichment_status",
]


class Block:
    def __init__(self, type, input=None):
        self.type, self.input = type, input


class Resp:
    def __init__(self, *blocks):
        self.content = list(blocks)


class FakeClient:
    """Imita client.messages.create(...)."""

    def __init__(self, behaviour):
        self.behaviour, self.calls, self.messages = behaviour, [], self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.behaviour(kwargs)


def tool_use(data):
    return FakeClient(lambda kw: Resp(Block("thinking"), Block("tool_use", data)))


class Validate(unittest.TestCase):
    def test_salida_correcta(self):
        self.assertEqual(en.validate(GOOD), GOOD)

    def test_intencion_invalida(self):
        self.assertIsNone(en.validate(dict(GOOD, analyzed_intent="Otra cosa")))

    def test_campos_que_invalidan_toda_la_salida(self):
        casos = {
            "score booleano": dict(GOOD, lead_value_score=True),
            "score texto": dict(GOOD, lead_value_score="5"),
            "alerta como texto": dict(GOOD, requires_immediate_alert="true"),
            "sin resumen": dict(GOOD, executive_summary_es=""),
            "sin siguiente paso": {k: v for k, v in GOOD.items() if k != "suggested_next_step_es"},
        }
        for nombre, salida in casos.items():
            with self.subTest(nombre):
                self.assertIsNone(en.validate(salida))
        for basura in (None, [], "texto"):
            with self.subTest(basura=basura):
                self.assertIsNone(en.validate(basura))

    def test_score_se_redondea_y_se_acota(self):
        for entrada, esperado in ((7, 5), (0, 1), (-3, 1), (4.6, 5), (3.2, 3)):
            with self.subTest(entrada=entrada):
                self.assertEqual(en.validate(dict(GOOD, lead_value_score=entrada))["lead_value_score"], esperado)

    def test_ruido_siempre_score_1(self):
        r = en.validate(dict(GOOD, analyzed_intent=en.NOISE, lead_value_score=4))
        self.assertEqual(r["lead_value_score"], 1)

    def test_departamento_invalido_usa_el_de_la_intencion(self):
        r = en.validate(dict(GOOD, analyzed_intent="Billing Disparity", target_department="Legal"))
        self.assertEqual(r["target_department"], "Finance")

    def test_resumen_sin_menciones_de_slack(self):
        r = en.validate(dict(GOOD, executive_summary_es="<!channel> urgente"))
        self.assertNotIn("<", r["executive_summary_es"])
        self.assertNotIn(">", r["executive_summary_es"])


class Fallback(unittest.TestCase):
    def test_clasificacion(self):
        casos = [
            ("El sistema está caído desde las 8 am y no podemos operar.",
             "Support Escalation", "Engineering Tier-2", 2, True),
            ("Nos cobraron dos veces la factura de octubre y si no lo corrigen cancelamos el contrato.",
             "Billing Disparity", "Finance", 2, True),
            ("Mi abogado les va a escribir por el incumplimiento.",
             "Support Escalation", "Account Management", 2, True),
            ("Our API is down since this morning",
             "Support Escalation", "Engineering Tier-2", 2, True),
            ("Necesitamos una cotización para 50 licencias. Presupuesto aprobado de USD 12,000 y queremos arrancar este mes.",
             "Sales High Intent", "Sales Operations", 5, False),
            ("Quisiera una demo del producto",
             "Sales High Intent", "Sales Operations", 4, False),
            ("¿Cuánto cuesta el plan? Solo estoy mirando.",
             "Sales High Intent", "Sales Operations", 3, False),
            ("Queremos explorar una alianza para distribuir su producto.",
             "Partnership Query", "Account Management", 2, False),
            ("jajaja gracias", en.NOISE, "Sales Operations", 1, False),
            ("Cancelar mi cita del jueves", en.NOISE, "Sales Operations", 1, False),
            ("", en.NOISE, "Sales Operations", 1, False),
        ]
        for texto, intencion, depto, score, alerta in casos:
            with self.subTest(texto=texto):
                r = en.fallback(texto)
                self.assertEqual(
                    (r["analyzed_intent"], r["target_department"], r["lead_value_score"], r["requires_immediate_alert"]),
                    (intencion, depto, score, alerta),
                )

    def test_la_salida_de_respaldo_pasa_la_validacion(self):
        for texto in ("El sistema está caído", "factura duplicada", "quiero una cotización", "hola", ""):
            with self.subTest(texto=texto):
                r = en.fallback(texto)
                self.assertEqual(en.validate(r), r)


class Enrich(unittest.TestCase):
    PAYLOAD = {
        "customer_id": "C-1042",
        "raw_message": "Necesitamos cotización <!channel> para 50 licencias",
        "metadata": {"source": "email", "timestamp": "2026-10-28T09:15:00Z"},
    }

    def test_exito(self):
        client = tool_use(GOOD)
        r = en.enrich(self.PAYLOAD, client)
        self.assertEqual(list(r), CAMPOS)  # mismo orden que el esquema
        self.assertEqual((r["customer_id"], r["enrichment_status"]), ("C-1042", "SUCCESS"))
        request = client.calls[0]
        content = request["messages"][0]["content"]
        self.assertNotIn("C-1042", content)       # customer_id no viaja al modelo
        self.assertNotIn("<!channel>", content)   # el texto del cliente no puede cerrar las etiquetas
        self.assertIn("2026-10-28", content)      # la metadata sí viaja
        self.assertNotIn("temperature", request)
        self.assertEqual(request["tool_choice"], {"type": "tool", "name": "record_enrichment"})

    def test_reintento_si_rechaza_la_herramienta_forzada(self):
        def behaviour(kwargs):
            if "tool_choice" in kwargs:
                raise ValueError("forced tool use not supported")
            return Resp(Block("tool_use", GOOD))

        client = FakeClient(behaviour)
        with mock.patch.object(en, "BAD_REQUEST", ValueError):
            r = en.enrich(self.PAYLOAD, client)
        self.assertEqual((r["enrichment_status"], len(client.calls)), ("SUCCESS", 2))
        self.assertNotIn("tool_choice", client.calls[1])

    def test_degrada_a_reglas(self):
        def timeout(kwargs):
            raise TimeoutError("timeout")

        casos = {
            "sin cliente": None,
            "timeout": FakeClient(timeout),
            "sin tool_use": FakeClient(lambda kw: Resp(Block("text"))),
            "salida inválida": tool_use({"analyzed_intent": "Otra cosa"}),
        }
        for nombre, client in casos.items():
            with self.subTest(nombre):
                r = en.enrich(self.PAYLOAD, client)
                self.assertEqual(r["enrichment_status"], "DEGRADED")
                self.assertEqual(r["customer_id"], "C-1042")
                self.assertEqual(en.validate({k: v for k, v in r.items() if k in GOOD}), {k: v for k, v in r.items() if k in GOOD})

    def test_el_modelo_no_puede_cambiar_customer_id_ni_status(self):
        sucio = dict(GOOD, customer_id="OTRO", enrichment_status="HACKED")
        r = en.enrich(self.PAYLOAD, tool_use(sucio))
        self.assertEqual((r["customer_id"], r["enrichment_status"]), ("C-1042", "SUCCESS"))
        self.assertNotIn("HACKED", json.dumps(r))

    def test_id_numerico_y_metadata_invalida(self):
        r = en.enrich({"customer_id": 77, "raw_message": "hola", "metadata": None}, None)
        self.assertEqual(r["customer_id"], "77")

    def test_temperatura_opcional(self):
        client = tool_use(GOOD)
        with mock.patch.object(en, "TEMPERATURE", 0.1):
            en.enrich(self.PAYLOAD, client)
        self.assertEqual(client.calls[0]["temperature"], 0.1)


# --------------------------------------------------------------------------- #
# API (se salta si faltan fastapi, httpx o anthropic)
# --------------------------------------------------------------------------- #

try:
    os.environ["SERVICE_API_KEY"] = "test-key"
    os.environ.pop("ANTHROPIC_API_KEY", None)
    from fastapi.testclient import TestClient

    import main
    HAY_API = True
except ImportError:
    HAY_API = False


@unittest.skipUnless(HAY_API, "instala requirements.txt para probar la API")
class Api(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.headers = {"X-API-Key": "test-key"}
        self.body = {"customer_id": "C-1042", "raw_message": "El sistema está caído", "metadata": {"source": "chat"}}

    def test_sin_api_key(self):
        self.assertEqual(self.client.post("/enrich", json=self.body).status_code, 401)

    def test_payload_incompleto(self):
        r = self.client.post("/enrich", json={"raw_message": "hola"}, headers=self.headers)
        self.assertEqual(r.status_code, 422)

    def test_degradado_sin_llm(self):
        r = self.client.post("/enrich", json=self.body, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(list(data), CAMPOS)
        self.assertEqual((data["enrichment_status"], data["requires_immediate_alert"]), ("DEGRADED", True))

    def test_id_numerico(self):
        r = self.client.post("/enrich", json=dict(self.body, customer_id=1042), headers=self.headers)
        self.assertEqual(r.json()["customer_id"], "1042")


if __name__ == "__main__":
    unittest.main()
