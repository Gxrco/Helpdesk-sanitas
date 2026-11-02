# SANITAS Service Desk — Clínica UVG

Implementación reproducible de **Frappe Helpdesk oficial**, self-hosted, para el caso de estudio SANITAS y las reglas ITIL v4 de SANITAS. Solo se usan identidades y casos sintéticos.

## Instalación local

Requisitos: Docker Engine/Desktop en ejecución, Docker Compose v2.24 o superior, Python 3.10 o superior y acceso a GHCR/Docker Hub. Reservar inicialmente 4 CPU, 8 GB RAM y 15 GB de disco para el laboratorio; ajustar con mediciones.

```sh
python3 scripts/sanitas.py init
python3 scripts/sanitas.py preflight
python3 scripts/sanitas.py install
python3 scripts/sanitas.py credentials
```

Abrir [Helpdesk local](http://helpdesk.localhost:8080/helpdesk). `localhost:8080` también llega al sitio porque Nginx fija el encabezado del sitio. Las contraseñas se generan localmente; `credentials` las muestra únicamente cuando se solicita ese comando. No hay contraseñas predeterminadas versionadas.

El instalador descarga imágenes, configura MariaDB/Redis, crea el sitio, instala Telephony y Helpdesk incluidos en la imagen, aplica configuración nativa, carga 18 casos y ejecuta las pruebas transaccionales de servidor. **Un error interrumpe la instalación:** no se presenta como exitosa si falla un paso. Después de resolverlo, puede repetirse; los tickets se identifican con claves únicas y no se vuelven a insertar. La configuración es para un sitio dedicado: reemplaza la plantilla Default y desactiva los SLA de fábrica.

Si Docker está detenido, iniciar Docker Desktop manualmente o Docker Engine en Linux y repetir `preflight`.

## Operación

```sh
python3 scripts/sanitas.py status
python3 scripts/sanitas.py stop
python3 scripts/sanitas.py start
python3 scripts/sanitas.py configure  # reaplica las reglas versionadas
python3 scripts/sanitas.py demo       # conserva los tickets demo existentes
python3 scripts/sanitas.py validate   # necesita Frappe instalado y datos demo
python3 -m unittest discover -s tests -v  # prueba de reglas sin Docker
docker compose logs --tail=100 backend queue-short queue-long scheduler
docker compose exec backend bench doctor
```

`stop` conserva los volúmenes. `docker compose down` también los conserva; **no usar `down -v` para reiniciar**, pues destruye los datos. Los workers y scheduler tienen reinicio automático; `bench doctor` y las pruebas funcionales complementan los health checks de conectividad, que no certifican por sí solos el funcionamiento de una cola.

## Accesos de demostración

| Usuario | Función | Equipo / permisos |
|---|---|---|
| `Administrator` | Configuración del sitio | Administración de Frappe |
| `solicitante@sanitas.example` | Solicita y consulta sus tickets | HD Customer, Website User |
| `solicitante2@sanitas.example` | Prueba de aislamiento | HD Customer, Website User |
| `nivel1@sanitas.example` | Recepción, clasificación y procedimientos conocidos | Agent / SANITAS Nivel 1 |
| `nivel2@sanitas.example` | Diagnóstico técnico teórico | Agent / SANITAS Nivel 2 |
| `nivel3@sanitas.example` | Código e infraestructura, solo a nivel teórico | Agent / SANITAS Nivel 3 |
| `responsable@sanitas.example` | SLA y decisiones del servicio | Agent Manager + Agent / SANITAS Responsable |

Las cuentas demo comparten una contraseña aleatoria local, distinta de Administrator. Son para el laboratorio; cambiarla o deshabilitarlas antes de exposición pública. Niveles 1–3 son equipos operativos sobre el rol nativo Agent, con cola central visible para facilitar escalamiento y swarming. No representan barreras de autorización entre agentes. Los solicitantes no tienen roles Agent ni System Manager. El responsable dispone de gestión de Helpdesk; la configuración de campos y permisos se realiza con Administrator.

## Funcionalidades preparadas

- Organización Clínica UVG, marca SANITAS Service Desk, zona `America/Guatemala`.
- Ocho módulos seleccionables y cuatro tipos nativos: Incidente, Solicitud de servicio, Problema, Cambio.
- Matriz automática de nueve combinaciones; corrección por soporte con justificación y Version de Frappe.
- Nueve SLA condicionales y una política de seguridad. Jornada lunes a viernes 08:00–20:30.
- Estados de Fase 1; `Closed` se conserva como equivalente a Cerrado, respetando su etiqueta y campos nativos protegidos.
- Escalamiento inmediato de riesgo de exposición a P1 y equipo responsable; seguimiento manual de incidentes repetidos y cambios.
- Dashboard nativo de Frappe con gráficos por tipo, módulo, prioridad, estado y resultado SLA.
- 18 tickets sintéticos con comentarios, responsables y relaciones incidente–problema.

## Recorrido de uso

1. Entrar como solicitante en `/helpdesk`, crear ticket con título, descripción, rol, tipo, módulo, impacto y urgencia. Adjuntar solo evidencia sintética mediante el adjuntador nativo.
2. Entrar como Nivel 1 y abrir Tickets. El caso ingresa a `SANITAS Nivel 1`; abrir su detalle, responder al usuario y dejar comentarios internos.
3. Clasificar y asignar agente/equipo. Para corregir prioridad activar **Corregir prioridad manualmente**, indicar prioridad y justificación. Sin esa opción la matriz prevalece al guardar.
4. Para escalar, cambiar equipo y responsable, marcar estado de escalamiento y registrar motivo y diagnóstico.
5. Al resolver, completar **Resolution Details / Resolución** y cambiar a Resuelto. Para cerrar, completar **Constancia de validación o espera hábil**, luego Closed. El solicitante también puede validar cerrando su ticket previamente resuelto.
6. En Desk abrir `/app/dashboard-view/SANITAS%20Service%20Desk` como Administrator. Los agentes con acceso a Desk pueden acceder según los permisos del dashboard y de los tickets. Para revisar la configuración usar `/app/dashboard-chart`; los gráficos no tienen acceso público.

## Arquitectura y versiones

| Componente | Imagen / función |
|---|---|
| Frappe Helpdesk | `ghcr.io/frappe/helpdesk:v1.31.0`, misma imagen en todos los procesos |
| Frappe Framework y Telephony | Incluidos en la imagen oficial; registrar versiones efectivas con `bench version` |
| MariaDB | `mariadb:10.6.24`, volumen `db-data` |
| Redis | `redis:7.2.7-alpine`, caché y cola separadas; cola con AOF y volumen |
| Backend | Gunicorn, puerto interno 8000 |
| Frontend | Nginx, puerto local 8080 |
| Tiempo real | Socket.IO, puerto interno 9000 |
| Workers | Colas short/default y long/default/short |
| Scheduler | `bench schedule`, scheduler del sitio habilitado |
| HTTPS opcional | `caddy:2.10.2-alpine`, certificados persistentes |

Los volúmenes `sites` y `logs` son compartidos por los procesos Frappe. MariaDB y Redis no publican puertos al host. Solo Nginx escucha en loopback por defecto. `toolbox` es un trabajo efímero: desde el directorio Bench ejecuta `site_task.py` con el Python de su entorno virtual. El script cambia a `sites/`, como hace la CLI de Bench, inicializa Frappe e importa los scripts de configuración directamente. Así las rutas relativas de logs y archivos apuntan a los volúmenes correctos. No registra una aplicación Frappe nueva. `bench execute sanitas_setup.apply` no se utiliza porque Frappe interpreta ese módulo como una aplicación instalada.

La imagen de Helpdesk incluye Telephony porque el build oficial lo requiere; no se habilitan servicios de telefonía. **ERPNext no se instala.** El build de fábrica usa Frappe v15 y referencias de apps en su pipeline; el proyecto consume el artefacto publicado con etiqueta fija, sin reconstruir desde ramas. Para preservar exactamente el binario, registrar su `RepoDigest` después de descargarlo y usar `HELPDESK_IMAGE=ghcr.io/frappe/helpdesk@sha256:…` en `.env`. Todavía no se pudo descargar ni inspeccionar ese digest en este entorno.

La documentación oficial recomienda `easy-install.py` con `stable`. Aquí se usa la topología oficial de [frappe_docker](https://github.com/frappe/frappe_docker/blob/main/compose.yaml), adaptada para la imagen Helpdesk, con etiqueta de release fija, secretos locales y health checks. La alternativa Docker de desarrollo del repositorio usa Bench, código montado y servidor de desarrollo; no se utiliza en esta entrega. El laboratorio ejecuta los mismos servicios de producción previstos para VPS, con HTTP limitado a la máquina local.

## Configuración frente a datos

| Archivo | Qué conserva |
|---|---|
| `compose.yaml`, `.env.example`, `deploy/` | Infraestructura y exposición |
| `config/policy.json` | Catálogo, usuarios ficticios, horario y objetivos SLA |
| `server_scripts/ticket_policy.py` | Automatización mínima de prioridad, seguridad y controles |
| `scripts/sanitas_setup.py` | Aplicación idempotente mediante Document API, Custom Fields y Property Setters oficiales |
| `config/demo.json` | Casos sintéticos con identificadores únicos |
| Respaldo de Bench | Tickets reales de la instancia, comentarios, adjuntos, usuarios y configuración vigente en BD |

`configure` reaplica valores gestionados por el repositorio; no importa automáticamente cambios hechos en la UI. Después de un ajuste administrativo, reflejarlo en `policy.json` o en el script correspondiente y revisar el diff. La configuración versionada permite reconstruir una instancia equivalente; para conservar datos y cambios no exportados se necesita respaldo. **Compose no exporta reglas de negocio de la base de datos.**

La operación `demo` no actualiza ni restablece tickets existentes; tampoco duplica comentarios al repetirla. La contraseña de un usuario existente no se cambia en una reaplicación. No ejecutar dos instalaciones o cargas demo concurrentes.

## Fuentes oficiales consultadas

- [Instalación self-hosted](https://docs.frappe.io/helpdesk/installation).
- [Repositorio y release de Helpdesk](https://github.com/frappe/helpdesk/releases/tag/v1.31.0).
- [Build oficial de la imagen del release](https://github.com/frappe/helpdesk/blob/v1.31.0/.github/workflows/build.yml).
- [Frappe Docker](https://github.com/frappe/frappe_docker).
- [SLA y horario de servicio](https://docs.frappe.io/helpdesk/service-level-agreement).
- [Visibilidad y niveles de permiso](https://docs.frappe.io/helpdesk/customization/perm-levels-in-helpdesk).
- [Server Scripts](https://docs.frappe.io/framework/user/en/desk/scripting/server-script).
