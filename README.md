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
docker compose run --rm migrate python -m paridad.cli create-admin docente --name "Docente administrador"
```

1. Abre <http://localhost:8787> e inicia sesión como `docente` con la contraseña que te pidió el comando.
2. En **Cuentas**, crea dos estudiantes. Las contraseñas tienen un mínimo de 12 caracteres y solo el administrador las asigna o restablece.
3. En **Actividades**, crea `lab01` y habilita **C**. Puedes elegir otros perfiles y definir umbrales para la actividad.
4. En sesiones de navegador independientes, sube un archivo `.c` por estudiante. Usa un programa de varias líneas y una segunda versión con nombres y formato cambiados. Consulta **Historial** en el primer navegador e **Informes → Ver fragmentos** en administración.

En el servidor usa el perfil TLS documentado en [Despliegue y Moodle](docs/despliegue-moodle.md). Si cambias el puerto local, cambia `PARIDAD_HTTP_PORT` y `PUBLIC_URL` para que ambos coincidan.

```sh
docker compose ps
docker compose logs worker app
curl --fail http://localhost:8787/healthz
docker compose down
```

El volumen `pgdata` conserva los datos al detener el proyecto. La aplicación y PostgreSQL se comunican por una red interna. El perfil TLS publica únicamente Nginx.

## Estructura

```text
paridad/
├── paridad/
│   ├── main.py, web.py, security.py   # HTTP, sesiones, CSRF y límites de tasa
│   ├── admin.py, student.py          # Administración, informes e historial propio
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

El JavaScript de producción está minificado y forma parte del repositorio. Si editas `static/student.js`, regenera el archivo servido con `npx --yes terser@5.48.0 static/student.js --compress --mangle -o static/student.min.js`.
