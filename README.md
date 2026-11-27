# Paridad

Paridad es una aplicación autoalojada para revisar similitud de código e indicios de IA en entregas universitarias. Incluye un portal de estudiantes, un panel para personal docente con rol `ADMIN`, informes de líneas coincidentes y un proceso en segundo plano que actualiza automáticamente el historial de ambos estudiantes.

## Arranque local

Usa Docker Engine con Docker Compose **2.24.4 o posterior** y OpenSSL. El perfil local escucha únicamente en `127.0.0.1:8787`.

```sh
umask 077
cp .env.example .env
sed -i "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$(openssl rand -hex 32)/" .env
sed -i "s/^PARIDAD_DB_PASSWORD=$/PARIDAD_DB_PASSWORD=$(openssl rand -hex 32)/" .env
docker compose up -d --build --wait
docker compose run --rm migrate python -m paridad.cli create-admin docente --name "Administrador"
```

1. Abre <http://localhost:8787> e inicia sesión como `docente` con la contraseña que te pidió el comando.
2. En **Cuentas**, crea dos estudiantes. Las contraseñas tienen un mínimo de 12 caracteres y solo el administrador las asigna o restablece.
3. En **Actividades**, crea `lab01` y habilita **C**. Puedes elegir otros perfiles y definir umbrales para la actividad.
4. En sesiones de navegador independientes, sube un archivo `.c` por estudiante. Usa un programa de varias líneas y una segunda versión con nombres y formato cambiados. Consulta **Historial** en el primer navegador e **Informes → Comparaciones → Ver** en administración.

En el servidor usa el perfil TLS documentado en [Despliegue y Moodle](docs/despliegue-moodle.md). Si cambias el puerto local, cambia `PARIDAD_HTTP_PORT` y `PUBLIC_URL` para que ambos coincidan.

```sh
docker compose ps
docker compose logs worker app
curl --fail http://localhost:8787/healthz
docker compose down
```

El volumen `pgdata` conserva los datos al detener el proyecto. La aplicación y PostgreSQL se comunican por una red interna. El perfil TLS publica únicamente Nginx.

## Actualizar una instalación

Actualiza el código en la misma carpeta donde ya ejecutas Paridad y conserva su archivo `.env`. Después ejecuta `docker compose --env-file .env up -d --build --wait --remove-orphans` desde esa carpeta. El servicio de migraciones aplicará el nuevo esquema sobre el volumen existente.

Si el PNG original se llama `Logotipo Paridad_….png` y está junto a `Dockerfile`, Docker lo incorpora al reconstruir la imagen. También puedes colocarlo directamente en `static/paridad-logo.png`.

## Gestionar estudiantes

Desde **Cuentas** puedes crear cuentas individuales, editar el correo y restablecer contraseñas. Para inhabilitar una cuenta, desmarca **Permitir iniciar sesión** y guarda el cambio. Paridad cierra las sesiones de la cuenta inhabilitada. La eliminación requiere escribir el ID y borra la cuenta, sus entregas y las comparaciones asociadas. El historial del resto de estudiantes refleja el nuevo máximo de similitud. Las casillas permiten inhabilitar, reactivar o eliminar varias cuentas a la vez. La eliminación en lote pide confirmación.

Para importar, descarga la plantilla en **Cuentas → Importar estudiantes**. Completa las columnas `ID`, `Nombre` y `Correo` en la primera hoja y sube el archivo `.xlsx`. Puedes importar hasta 200 filas y 128 KiB por archivo. Guarda los IDs con ceros iniciales como texto. Si un ID o correo está repetido o hay una fila inválida, se rechaza el archivo completo y se indica la fila.

Al pulsar **Importar**, se descarga automáticamente `paridad-credenciales.xlsx`. Contiene las columnas **ID** y **Contraseña**, con una contraseña inicial aleatoria distinta por estudiante. PostgreSQL guarda los hashes y el administrador puede restablecer contraseñas desde **Cuentas**.

En **Cuentas → Acciones** puedes modificar, inhabilitar, reactivar o eliminar una cuenta. Para que un estudiante elija su contraseña, selecciona **Generar enlace de invitación** y pulsa **Copiar enlace**. El enlace es personal, sirve una vez y caduca en 24 horas. Generar otro reemplaza el anterior.

## Cuenta y entregas del administrador

Desde el menú de la cuenta, abre **Mi cuenta**. Para cambiar el usuario o la contraseña, confirma tu contraseña actual. Paridad cierra las otras sesiones de esa cuenta y renueva la sesión actual.

En **Archivos**, abre **Acciones** para ver, descargar o eliminar una entrega. Marca varias entregas para descargarlas en un ZIP o eliminarlas tras confirmar. **Todos los archivos del filtro** abarca todas sus páginas. Al eliminar una entrega también se eliminan sus comparaciones. El historial de los estudiantes afectados se recalcula. Paridad guarda los bytes originales de las nuevas entregas. Las entregas anteriores a esta actualización se descargan como texto UTF-8 a partir del código almacenado.

En **Cuentas**, **Informes**, **Aclaraciones** y **Archivos** puedes buscar estudiantes por nombre o ID. Los filtros se aplican al cambiar y las descargas y acciones en lote respetan el filtro activo.

En la pestaña **Entregas** de **Informes**, **Descargar Excel** está al lado de la selección. Si marcas archivos, el libro incluye esas entregas y sus comparaciones. Marca una o varias entregas para habilitar la descarga. **Todos** incluye todas las entregas de los filtros activos. Desde **Comparaciones**, abre **Ver** para revisar ambos archivos con las líneas coincidentes resaltadas. Los grupos reúnen archivos de la misma actividad y lenguaje conectados por coincidencias por encima del umbral.

## Panel de administración

Filtra la participación, las alertas y las notas por módulo, actividad o lenguaje. La participación cuenta estudiantes únicos con al menos una entrega y se calcula sobre las cuentas inscritas. Las tasas de alerta se calculan sobre las entregas analizadas.

Al guardar los umbrales globales, todas las actividades adoptan esos valores. Las actividades nuevas los heredan. Puedes ajustar por separado una actividad existente.

## Módulos y plazos

En **Actividades**, crea un módulo como **LAB01** y asigna al módulo las actividades **LAB01A** y **LAB01B**. Cada actividad tiene sus propios lenguajes y horarios. Los horarios se interpretan en `PARIDAD_TIME_ZONE` (por defecto, `America/Bogota`). Al abrir un módulo desde **Mis entregas**, se muestran sus actividades, sus plazos y su historial. **Subir archivo** abre la ventana de envío y **Todos los módulos** muestra el historial completo.

Abre un módulo para gestionar sus actividades o editar y eliminar el módulo. En **Acciones** puedes editar una actividad, cerrar o reabrir entregas y eliminarla tras confirmar. Reabrir conserva el plazo configurado. Eliminar una actividad borra sus archivos, comparaciones y aclaraciones vinculadas. La migración asigna cada actividad existente a un módulo de su mismo código y conserva su estado y sus entregas.

## Notas, avisos y aclaraciones

Cada estudiante puede tener hasta tres archivos en total. El administrador puede liberar cupo eliminando una entrega.

La nota automática combina hasta 35 puntos de calidad estática del código y 15 puntos de base de originalidad. La calidad evalúa estructura (10) y legibilidad (5), identificadores (5), buenas prácticas (5), comentarios (5) y mantenibilidad (5). Paridad resta hasta 30 puntos, en proporción a la máxima similitud, con un mínimo final de 0. Con 35 puntos de calidad, una similitud del 50 % da 35/50 y una del 100 % da 20/50. El trabajador actualiza la rúbrica de las entregas anteriores y se respetan las notas ajustadas por el administrador. Los indicios de IA se usan para avisos de revisión.

El estudiante consulta el desglose desde su historial y puede solicitar una aclaración por archivo. En **Aclaraciones**, en el menú superior, el administrador puede responder, resolver o rechazar la solicitud y ajustar la nota. El Excel distingue la nota automática, la docente y la final.

En **Informes → Entregas** puedes seleccionar archivos y pulsar **Enviar aviso** para publicar un mensaje por estudiante. En **Archivos**, el menú **Acciones** permite publicar un aviso individual. Los estudiantes abren cada mensaje en **Avisos** y Paridad lo marca como leído.

## Estructura

```text
paridad/
├── paridad/
│   ├── main.py, web.py, security.py   # HTTP, sesiones, CSRF y límites de tasa
│   ├── admin.py, activities.py, student.py # Administración, módulos e historial propio
│   ├── analysis.py, worker.py        # Análisis léxico y recálculo transaccional
│   └── config.py, db.py, cli.py      # Configuración, PostgreSQL y comandos locales
├── migrations/001_initial.sql       # Esquema, índices y perfiles iniciales
├── templates/, static/              # HTML escapado, CSS y JavaScript del estudiante
├── deploy/                          # Configuración Nginx local y TLS
├── docs/                            # Arquitectura, motor, operación y Moodle
├── tests/                           # Pruebas del motor y del flujo HTTP
├── examples/                        # Archivos C sintéticos y demostración
├── .env.example
├── docker-compose.yml
├── compose.tls.yml
└── Dockerfile
```

- [Arquitectura, modelo PostgreSQL y motor](docs/arquitectura.md)
- [Despliegue HTTPS, Moodle y operación](docs/despliegue-moodle.md)

## Demostración

Abre `examples/demo-paridad.html` en tu navegador para recorrer el ejemplo de recálculo de similitud y ver sus fragmentos como docente. La demostración reproduce resultados obtenidos con los dos archivos C de ejemplo.

Para probar el flujo completo en una instancia de Paridad, crea una actividad para C y sube `examples/suma_pares.c` y `examples/suma_renombrada.c` con cuentas de estudiantes distintas. Tras analizar la segunda entrega, el historial de la primera se actualiza automáticamente.

## Verificación

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

Para la prueba HTTP, arranca una instancia **de prueba** y crea el administrador con el comando anterior. La prueba genera estudiantes, lenguajes y actividades con sufijos únicos. Restablece los umbrales globales a 40 y 30 en esa instancia antes de ejecutarla.

```sh
PARIDAD_TEST_ADMIN=docente python tests/smoke_http.py
```

La prueba solicita la contraseña de forma interactiva. Para automatizarla en una instancia aislada, habilita `PARIDAD_TEST_PASSWORD` en el entorno de ese proceso. Por defecto usa `http://localhost:8787`. Configura `PARIDAD_TEST_URL` y, para un certificado de prueba, `PARIDAD_TEST_CA_FILE`. El script verifica TLS con las raíces del sistema o la CA declarada.

El JavaScript de producción está minificado y forma parte del repositorio. Si editas alguno de los scripts, regenera su archivo servido.

```sh
npx --yes terser@5.48.0 static/student.js --compress --mangle -o static/student.min.js
npx --yes terser@5.48.0 static/navigation.js --compress --mangle -o static/navigation.min.js
```
