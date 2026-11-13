# nubeduca-ms-usuarios

Microservicio de **tenants, periodos académicos y perfiles** de Nubeduca. Se creó desde [`nubeduca-plantilla-servicio`](https://github.com/nubeduca/nubeduca-plantilla-servicio) (LIB-01) y sigue el contrato [`apis/ms-usuarios.openapi.yaml`](https://github.com/nubeduca/nubeduca-contratos/blob/main/apis/ms-usuarios.openapi.yaml).

| Tarea | Responsable | Estado |
|---|---|---|
| ACA-01 · Tenant y Organización | Dev 3 @btoroled | Tenants, periodos y eventos `tenant.created` y `term.started` |
| ACA-02 · Usuarios y Perfiles | Dev 4 @Edinson695 | Pendiente (`/users/me`, `/users`, seed de 30.000 perfiles) |

## API (ACA-01)

El tenant sale siempre del token (`custom:tenant_id`, o `Bearer mock.<tenant>.<ROL>.<userId>` en modo mock), nunca de la ruta ni del body.

| Método y ruta | Roles | Qué hace | Evento |
|---|---|---|---|
| `GET /health` | pública | Prueba de vida | |
| `GET /tenant` | STUDENT, TEACHER, ADMIN | Datos del tenant del token | |
| `GET /terms?limit=&nextToken=` | STUDENT, TEACHER, ADMIN | Periodos, el más reciente primero, paginados | |
| `POST /terms` | ADMIN | Crea un periodo en `PLANNED`. 409 si el nombre ya existe en el tenant | |
| `POST /terms/{termId}/start` | ADMIN | `PLANNED` → `ACTIVE`. 409 si no está en `PLANNED` | `lms.users.term.started.v1` |

Los errores tienen la forma `{code, message, requestId}`: `NO_AUTENTICADO`, `SIN_PERMISO`, `DATOS_INVALIDOS`, `TENANT_NO_ENCONTRADO`, `PERIODO_NO_ENCONTRADO`, `PERIODO_DUPLICADO` y `PERIODO_NO_PLANIFICADO`.

## Eventos que publica

| Evento | Cuándo | Consumidores |
|---|---|---|
| `lms.users.tenant.created.v1` | `scripts/poblar_datos.py` crea un tenant (una vez por tenant) | ninguno todavía |
| `lms.users.term.started.v1` | `POST /terms/{termId}/start` | ms-cursos |

Todos salen por **outbox**: el cambio y el ítem `EVENTO#<eventId>` van en la misma `TransactWriteItems`, y antes se validan contra su JSON Schema de `nubeduca-contratos` (`contratos/esquemas/`). Si el evento no cumple el contrato, no se guarda nada. ms-usuarios no consume eventos.

## Tabla `<stage>-t_usuarios`

PK `tenant_id` (HASH) + `sk` (RANGE), `PAY_PER_REQUEST`, Stream `NEW_IMAGE` y TTL en `ttl`.

| `sk` | Entidad | Ejemplo |
|---|---|---|
| `TENANT` | Institución | ver abajo |
| `TERM#<termId>` | Periodo académico | ver abajo |
| `TERMNAME#<name>` | Reserva del nombre del periodo en el tenant | `{"tenant_id": "utec", "sk": "TERMNAME#2026-2", "entidad": "TERMNAME", "termId": "trm_01JC..."}` |
| `EVENTO#<eventId>` | Outbox (lo lee el Stream) | `source`, `detailType`, `detail`, `ttl` 7 días |

```json
{
  "tenant_id": "utec",
  "sk": "TENANT",
  "entidad": "TENANT",
  "name": "Universidad de Ingeniería y Tecnología",
  "timeZone": "America/Lima",
  "locale": "es-PE",
  "version": 1,
  "createdAt": "2026-11-13T15:00:00Z"
}
```

```json
{
  "tenant_id": "utec",
  "sk": "TERM#trm_01JCP0KWJDXF9GJ9YNTK2H84A9",
  "entidad": "TERM",
  "termId": "trm_01JCP0KWJDXF9GJ9YNTK2H84A9",
  "name": "2026-2",
  "startsOn": "2026-08-17",
  "endsOn": "2026-12-12",
  "status": "ACTIVE",
  "version": 2,
  "createdBy": "16248d98-4e7a-41b9-a08b-4929177d2e2d",
  "createdAt": "2026-11-13T15:00:00Z",
  "startedBy": "16248d98-4e7a-41b9-a08b-4929177d2e2d",
  "startedAt": "2026-11-13T15:05:00Z"
}
```

`termId` es un ULID, así que ordenar por `sk` de forma descendente da el periodo creado más recientemente primero.

## Probar

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Las pruebas usan moto (sin AWS). Cubren permisos por rol, aislamiento por tenant, validación del body, 409 por nombre repetido o periodo ya iniciado, paginación, idempotencia del seed de tenants y el recorrido completo del evento: outbox → Stream → relay → bus → regla de ms-cursos → su cola.

## Desplegar

En la cuenta de integración (D2) se despliega junto con todo lo demás desde la VM `nubeduca-despliegue`: `scripts/desplegar.sh staging --con-seed` de [`nubeduca-backend`](https://github.com/nubeduca/nubeduca-backend), o solo este servicio con `--solo usuarios`.

**Observabilidad (PLT-06):** X-Ray en API Gateway y Lambda (ms-usuarios no consume eventos, así que no tiene DLQ).

Para desplegarlo a mano en tu propia cuenta, con las credenciales cargadas (en `nubeduca-backend`: `source scripts/cuenta.sh && cargar_cuenta`):

```bash
serverless deploy --stage dev
python scripts/poblar_datos.py --stage dev     # crea utec, upc y pucp y publica tenant.created
```

El despliegue publica la URL de la API en SSM, en `/nubeduca/<stage>/api/usuarios/url`. De ahí la lee el stack de Amplify (`infra/frontend` de `nubeduca-backend`).

Para actualizar los esquemas cuando cambie el catálogo: `python scripts/copiar_esquemas.py --solo lms.users`.
