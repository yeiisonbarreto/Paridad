# Despliegue HTTPS y Moodle

## Nginx y TLS en el servidor universitario

1. Haz que el DNS institucional apunte el nombre de Paridad al servidor. Instala un certificado vigente para ese nombre. Usa `fullchain.pem` y `privkey.pem` en un directorio legible por el Nginx de Compose y restringido en el host.
2. Prepara `.env` con las dos claves generadas en el [README](../README.md). Ajusta `PUBLIC_URL` al origen exacto que usarán los navegadores y `PARIDAD_TLS_DIR` al directorio del certificado. La zona por defecto para el historial es `America/Bogota`. Puedes cambiarla con `PARIDAD_TIME_ZONE`.
3. Arranca la variante TLS y crea el administrador inicial en la consola.

```dotenv
PUBLIC_URL=https://paridad.universidad.edu
PARIDAD_TLS_DIR=/etc/paridad/tls
PARIDAD_TLS_BIND=443
MOODLE_FRAME_ORIGINS=
```

```sh
docker compose -f docker-compose.yml -f compose.tls.yml up -d --build --wait
docker compose -f docker-compose.yml -f compose.tls.yml run --rm migrate \
  python -m paridad.cli create-admin docente --name "Administrador"
curl --fail https://paridad.universidad.edu/healthz
```

El perfil TLS publica HTTPS. Coordina la emisión y renovación de certificados con el servicio institucional. Tras renovar archivos dentro del directorio montado, recarga Nginx. Conserva `.env` con permisos restrictivos. El servicio de migraciones usa el propietario de PostgreSQL y prepara un rol de operación sin permisos para alterar el esquema ni administrar roles. La aplicación y el trabajador reciben únicamente la credencial del rol de operación.

## Avisos y acceso de los estudiantes

**Avisos** muestra al estudiante sus mensajes y el contador de pendientes de lectura. Al abrir un mensaje en la ventana flotante, Paridad lo marca como leído. Los avisos automáticos nuevos guardan el resultado y el umbral vigentes al publicarse. Los avisos manuales de Paridad muestran las entregas propias incluidas en la selección. Paridad publica un aviso cuando una entrega supera el umbral de similitud o de indicio de IA sin declarar. El trabajador revisa también las entregas anteriores y registra cada tipo de alerta una vez por archivo. Los avisos propios se actualizan aproximadamente cada diez segundos con la página abierta.

En **Informes**, selecciona entregas y pulsa **Enviar aviso** y confirma para publicar un mensaje por estudiante. La confirmación aparece en la misma página. El menú **Acciones** de Archivos permite hacerlo para uno solo. Paridad omite las cuentas inactivas y evita publicar avisos manuales repetidos al mismo estudiante en un minuto. La migración conserva los avisos anteriores en la bandeja, inicialmente como pendientes de lectura. Los sonidos de envío y recepción se activan al interactuar con la página y dependen de la configuración de audio del navegador.

Una solicitud de aclaración genera una confirmación de recepción. Cuando se publica la decisión en **Aclaraciones**, Paridad actualiza el mismo hilo y lo marca como pendiente de lectura. El hilo muestra el motivo original, la respuesta y las notas registradas al solicitar y resolver. El menú del panel docente indica cuántas aclaraciones están pendientes.

En **Cuentas**, abre una cuenta activa y usa **Generar enlace de invitación** para obtener un enlace personal que caduca en 24 horas. Generar otro enlace invalida el anterior. Usarlo, cambiar el correo, restablecer la contraseña o inhabilitar la cuenta también lo invalida.

Al actualizar una instalación existente, añade `--remove-orphans` al comando de arranque de Compose para retirar los servicios obsoletos del proyecto y conserva su `.env` y sus datos de PostgreSQL.

## Moodle con sesión local

| Modalidad | Configuración | Ventaja | Dependencia |
|---|---|---|---|
| Recurso **URL** | Agrega la URL HTTPS de Paridad y selecciona presentación en nueva ventana o navegación de página completa | Menos dependencia de cookies en terceros y puesta en marcha directa | Los estudiantes inician sesión en Paridad con una cuenta asignada |
| Recurso **URL → Incrustar** o iframe autorizado | Configura el origen exacto de Moodle en `MOODLE_FRAME_ORIGINS` y usa la URL HTTPS de Paridad | Permite ver el portal estudiantil dentro del curso | Requiere permisos de Moodle para incrustar y compatibilidad del navegador con la cookie en ese contexto |
| **Herramienta externa LTI 1.3** | Registrar Moodle como plataforma LTI y Paridad como herramienta tras implementar el lanzamiento | Facilita inicio federado y vincula el recurso con el curso o actividad | Requiere apoyo de administradores Moodle y desarrollo de OIDC/LTI |

Para incrustar, por ejemplo:

```dotenv
PUBLIC_URL=https://paridad.universidad.edu
MOODLE_FRAME_ORIGINS=https://moodle.universidad.edu
```

Si se autorizan varios Moodle, sepáralos por comas. Acepta únicamente orígenes HTTPS del centro, con puerto explícito si corresponde. La lista pasa a `frame-ancestors` para las rutas del portal y la entrada. Las rutas administrativas mantienen `frame-ancestors 'none'`. Los navegadores verifican todos los ancestros. Cualquier política `frame-src` del Moodle también debe permitir el origen de Paridad. Si el iframe tiene atributo `sandbox`, el portal requiere scripts, formularios y conservación del origen.

Con la incrustación habilitada, las cookies son `Secure; HttpOnly; SameSite=None`. Prueba la entrada y la subida con los navegadores institucionales. Cuando Moodle y Paridad sean de sitios distintos, la política de cookies de terceros del navegador puede bloquear la sesión. Moodle puede mantener un recurso URL en página completa como alternativa. El caso de dos subdominios HTTPS del mismo sitio suele reducir esa dependencia. Un Moodle público que incruste una instancia de Paridad en una red privada también queda sujeto a los permisos de acceso a red local del navegador.

### Integración planificada con LTI 1.3

La entrega implementa el recurso URL y el iframe con sesión local. El diseño LTI contempla registrar en Moodle el inicio OIDC, la URI de lanzamiento y el JWKS de la herramienta. La configuración local guarda el emisor, ID de cliente, ID de despliegue, URL de autorización y JWKS de la plataforma como valores administrados. Moodle puede abrir el lanzamiento en una ventana de nivel superior.

El inicio OIDC debe emitir `state` y `nonce` de un solo uso con vencimiento y devolver el control al emisor configurado. En el lanzamiento, la herramienta verifica firma, algoritmo, emisor, audiencia y parte autorizada si aplica, vencimiento, `nonce`, `state`, tipo de mensaje, versión, ID de despliegue y vínculo de recurso. Obtiene claves desde los JWKS institucionales previamente configurados y contempla rotación. El vínculo de Moodle se asocia a la actividad de Paridad explícitamente. Una identidad `iss` + `sub` se vincula a una cuenta activa preexistente mediante una decisión administrativa. La sesión de aplicación se crea después de esa vinculación. Un rol de Moodle por sí solo carece de efecto sobre el rol local de administrador.

- [Configuración del recurso URL en Moodle](https://docs.moodle.org/en/URL_resource_settings)
- [Configuración de herramientas externas en Moodle](https://docs.moodle.org/en/External_tool_settings)
- [Especificación LTI 1.3 de 1EdTech](https://www.imsglobal.org/spec/lti/v1p3/)

## Seguridad y operación

- **Sesiones.** Argon2id para contraseñas y tokens opacos aleatorios de ocho horas, con digest SHA-256 en PostgreSQL. HTTPS usa cookies con prefijo `__Host-`, sin atributo `Domain`, `HttpOnly` y `Secure`. El modo local HTTP habilita cookies sin `Secure` solo con un nombre de host de bucle local. El cierre revoca la sesión y el restablecimiento o la desactivación revoca las sesiones del estudiante.
- **Autorización y CSRF.** FastAPI verifica el rol para cada recurso protegido y usa el ID autenticado para consultar entregas propias. Cada POST exige origen exacto y token CSRF. La entrada usa un reto temporal de formulario y cookie. El backend identifica alumnos a partir de la sesión, sin aceptar un ID de propietario enviado por el navegador.
- **Archivos, XSS y SQLi.** Nginx y FastAPI limitan el cuerpo a 144 KiB. FastAPI valida el archivo a 128 KiB, extensión por perfil, nombre, texto UTF-8 y límites léxicos. Las fuentes se guardan como texto en PostgreSQL. Jinja escapa datos y código, la CSP limita recursos y las consultas usan parámetros SQL. La interfaz no envía código de terceros, parejas ni identificadores de compañeros a estudiantes.
- **Límites de tasa.** Nginx aplica un límite general por IP. PostgreSQL mantiene ventanas de 15 minutos para el inicio de sesión (80 por IP, 12 por usuario) y de 10 minutos para las entregas (12 por alumno). Los rechazos usan HTTP 429. Si Nginx queda detrás de otro proxy, configura allí el módulo de IP real con rangos explícitos antes de tomar `$remote_addr` como la IP del cliente. El puerto de FastAPI permanece privado.
- **Controles en el navegador.** El historial del estudiante inhibe clic derecho, copia y selección en su zona de resultados, y disuade atajos de inspección habituales cuando el navegador permite interceptarlos. La versión de JavaScript servida está minificada. Estas medidas son disuasorias y se pueden desactivar con `CLIENT_DETERRENCE=false`. La protección de datos y permisos reside en el servidor. Una persona puede inspeccionar y guardar los datos que su navegador recibe.
- **Respaldo.** Usa `pg_dump` y una política institucional de copias cifradas con acceso restringido. Prueba la restauración en una instancia aislada. Define retención académica para fuentes, métricas, informes y copias. Restringe el acceso al volumen y a las credenciales.
- **Versionado.** Haz copias antes de actualizar imágenes o migraciones. Un cambio de versión de análisis debe reindexar las entregas históricas del ámbito de manera coordinada. Las métricas históricas de distintas versiones requieren sus propias reglas de comparación.

Ejemplo de respaldo al volumen de archivos del operador:

```sh
umask 077
docker compose -f docker-compose.yml -f compose.tls.yml exec -T db \
  pg_dump -U paridad_owner -d paridad -Fc > paridad.dump
```
