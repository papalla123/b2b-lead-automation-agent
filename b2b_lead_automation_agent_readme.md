# 🚀 Agente de Automatización B2B: Cualificación, Enriquecimiento e Integración de Leads

Sistema automatizado *end-to-end* diseñado para capturar, validar, enriquecer y cualificar leads B2B en tiempo real. Combina orquestación no-code (Make) para integraciones de entrada/salida con una arquitectura de backend en Python orientada a testing y reglas de negocio dinámicas (módulo de decisión Tier-2).

---

## 📽️ Demostración en Vivo
> 🎬 **[Haz clic aquí para ver la demostración en video del flujo (35s)]https://youtu.be/od52nhuLWKI**

---

## 🛠️ Arquitectura del Sistema y Flujo de Datos

```text
[ Incoming Payload / HTTP Webhook ]
               │
               ▼
   [ Make Orchestrator Router ]
               │
               ├─────────────────────────────────────────┐
               ▼                                         ▼
   [ HubSpot CRM Integration ]            [ Python Enrichment & Logic Engine ]
(Create/Update Contact Record)            (Validation, Scoring & Fallback Rules)
               │                                         │
               └────────────────────┬────────────────────┘
                                    ▼
                         [ Gmail API Notification ]
                         (Instant Alert for HOT Leads)
```

1. **Captura (HTTP Webhook):** Entrada del payload JSON con la interacción del prospecto.
2. **Sincronización CRM (HubSpot):** Creación o actualización automática del perfil comercial.
3. **Módulo de Decisión & Enriquecimiento (`enrichment.py` / Python):**
   * Evaluación de 6 campos clave de análisis (`customer_id`, `intention`, `score`, `department`, `summary`, `alert_flag`).
   * Validación estricta de esquemas y lógica de *fallback* mediante reglas de negocio locales en caso de fallos de modelos de lenguaje/API.
4. **Disparo de Alertas (Gmail API):** Notificación inmediata al equipo de ventas cuando se identifica un lead de alta prioridad (`HOT`).

---

## 🧰 Stack Tecnológico

* **Lenguaje:** Python 3.11+
* **Frameworks & Utility Libraries:** `json`, `logging`, `re`, `unicodedata`, `typing`
* **Testing:** `unittest` (Suite completa de integración y unidad)
* **Orquestación:** Make (Integromat)
* **Integraciones:** HubSpot CRM API, Gmail API / SMTP, HTTP Webhooks

---

## 🧪 Pruebas Unitarias y Calidad de Código

El proyecto cuenta con una suite de tests unitarios que garantizan la integridad de la lógica de cualificación y limpieza de payloads antes de enviar datos al CRM.

Para ejecutar los tests locales:
```bash
python test_leads.py
```

**Resultado esperado:**
```text
sssss........................
----------------------------------------------------------------------
Ran 24 tests in 0.007s

OK (skipped=5)
```

---

## ⚙️ Instalación y Configuración Local

### 1. Clonar el repositorio
```bash
git clone https://github.com/tu-usuario/b2b-lead-automation-agent.git
cd b2b-lead-automation-agent
```

### 2. Configurar el entorno virtual e instalar dependencias
```bash
python -m venv venv
# En Windows:
venv\Scripts\activate
# En Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configurar variables de entorno
Crea un archivo `.env` en la raíz basándote en la plantilla de configuración:
```env
HUBSPOT_API_KEY=tu_api_key_aqui
GMAIL_SENDER_EMAIL=tu_correo@dominio.com
WEBHOOK_SECRET_TOKEN=tu_token_seguro
```

---

## 🛡️ Principios de Desarrollo
* **Clean Code & Modularidad:** Separación limpia de la capa de orquestación, lógica de negocio y tests.
* **Manejo de Errores Robustos:** Lógica defensiva para tolerar payloads corruptos o incompletos.
* **Testing-First:** Validación automatizada para evitar regresiones en la cualificación de clientes.