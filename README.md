# Accesos Directos

App de escritorio (PySide6 / Qt) para organizar accesos directos a archivos y
carpetas en tarjetas, con carpetas internas, árbol lateral, temas de color,
arrastrar y soltar, y auto-actualización **privada** desde la propia empresa.

## Archivos

- `main.py` — la aplicación (interfaz, tarjetas, carpetas, drag&drop, configuración).
- `app_config.py` — rutas, ajustes, modelo de datos y origen de las actualizaciones.
- `github_updates.py` — comprobación e instalación de actualizaciones (carpeta de red, servidor interno o GitHub). El nombre se conserva por compatibilidad con instalaciones existentes.
- `publicar.py` — herramienta para **publicar** (y firmar) una versión en la carpeta/servidor de la empresa.
- `generar_claves.py` — crea una sola vez las claves de firma de las actualizaciones.
- `win_icons.py` — iconos reales de Windows (vía `ctypes`) y miniaturas de imagen (Pillow).
- `main.spec` — receta de PyInstaller (modo onedir).
- `requirements.txt`, `iniciar.bat` / `iniciar.sh`, `VERSION`.

## Soltar una carpeta = importar su árbol

Al arrastrar una **carpeta** del Explorador a la ventana (o sobre una carpeta
interna), no se crea un único acceso a ella: se crea una carpeta interna con el
mismo nombre y, dentro, sus subcarpetas (carpetas internas) y sus archivos
(accesos directos), respetando la estructura. Se omiten ocultos/de sistema y
enlaces simbólicos. Límite de 10000 elementos (`FOLDER_IMPORT_MAX_ITEMS`); solo
se pregunta si hay que recortar. Se puede deshacer con Ctrl+Z de una sola vez.
El botón «Añadir carpeta» sigue creando un acceso directo normal a la carpeta.

## Accesos a carpetas se abren dentro de la app

Si un acceso apunta a una **carpeta** (o a un `.lnk` cuyo destino es una carpeta), al
abrirlo ya no se lanza el Explorador: la app lee la carpeta y la convierte en una carpeta
interna con todo su árbol dentro, y navegas por ella con las migas de pan y el árbol lateral.
Los accesos a carpetas que haya dentro se van convirtiendo igual, según los abres.

- Es una **copia** del contenido en el momento de abrirla. Para releer el disco: clic derecho >
  **Actualizar desde la carpeta original** (sustituye lo que hubiera dentro; se puede deshacer).
- Para verla en el Explorador: clic derecho > **Abrir en el Explorador**.
- Si la carpeta no se puede leer, se abre en el Explorador como antes.
- La conversión se puede deshacer con Ctrl+Z.

## Búsqueda y carpetas sin explorar

La búsqueda recorre todo lo que hay importado en la app. Los accesos a carpetas que
aún no se han abierto no tienen contenido importado, así que al buscar aparece un
aviso con **Explorar ahora** / **Ahora no**: el primero las importa todas de una vez
(con barra de progreso y cancelable; se deshace con Ctrl+Z).

## Nombres largos en las tarjetas

El texto de cada tarjeta se ajusta solo a su tamaño (pequeña, mediana, grande o la que
hayas dado arrastrando la esquina) y nunca se pisa con el icono: primero se prueba el
tamaño normal; si no cabe, el subtítulo pasa a una línea, luego la letra baja hasta
10 px con menos margen, y si todavía no cabe se recorta con «…». Al pasar el ratón por
una tarjeta recortada se ve el nombre completo. Lo mismo en la vista compacta.

## Rendimiento

- Solo se dibujan 300 tarjetas de golpe por vista (carpetas y resultados de búsqueda enormes);
  el resto se carga con **Mostrar más**. Ctrl+A selecciona las tarjetas dibujadas.
- La exploración de carpetas y la detección de «carpetas sin explorar» se hacen en segundo
  plano (4 carpetas en paralelo); el progreso se ve en la franja de la búsqueda, sin ventanas.
- El árbol lateral solo se reconstruye si está visible; «deshacer» y el guardado son mucho
  más rápidos con miles de elementos (JSON compacto y escritura atómica).

## Detección automática de actualizaciones

Al arrancar, cada 10 minutos y al volver a la ventana (si hace más de 3 min) se consulta
`latest.json` del origen configurado. Si hay versión nueva aparece una franja con
**Actualizar ahora / Más tarde** (y una notificación si la app está en la bandeja); no hay
ventanas emergentes. Si falla (red de empresa no disponible, falta el paquete `exe`...), el
icono pasa a ⚠️ con el motivo en el tooltip, se reintenta al minuto (hasta 3 veces) y todo
queda en `log.txt` (versión instalada y publicada, errores). Se puede desactivar en
Configuración («Buscar actualizaciones automáticamente»).

## Actualizaciones privadas (sin GitHub público)

Se elige el origen en `app_config.py` (`UPDATE_SOURCE`) o, sin recompilar,
con un `update_config.json` junto al `.exe`:

```json
{ "source": "folder", "folder": "\\\\servidor\\software\\AccesosDirectos" }
```

| `source`  | Qué es | Cuándo usarlo |
|-----------|--------|---------------|
| `folder`  | Carpeta compartida de red (UNC/unidad) | Lo más simple: sin servidor ni tokens. Acceso por permisos AD/NTFS. |
| `http`    | Servidor web interno que sirve `latest.json` + los `.zip` (`url`, `token` opcional) | Si los equipos no ven la carpeta de red (VPN, sedes). |
| `github`  | GitHub Releases (público, o privado con token) | Comportamiento anterior. |

### Publicar una versión

1. Sube el número en `VERSION` (ej. `2.5.1.0`).
2. En un PC Windows con el proyecto: `py publicar.py --build --notes "Qué cambia"`
   (compila con PyInstaller, empaqueta, calcula SHA-256, copia los `.zip` y escribe
   `latest.json` el último, de forma atómica). Sin `--build` usa lo que ya haya en `dist/main`.
3. Los clientes lo detectan solos (al arrancar / cada 15 min / botón 🔄), verifican el
   SHA-256 y se actualizan.

### Error «CERTIFICATE_VERIFY_FAILED» al buscar actualizaciones

Pasa en redes que inspeccionan HTTPS (proxy/antivirus de empresa) o en equipos sin los
certificados raíz al día (p. ej. un Windows 10 sin actualizar). La app verifica el
certificado con `truststore` (certificados de Windows) y, si falla, con el almacén de
Python; nunca se desactiva la verificación. Antes de compilar: `pip install -r requirements.txt`.
Si aun así falla, guarda el certificado de la empresa (formato PEM) como `ca_bundle.pem`
junto a la app, actualiza Windows y revisa que la fecha y hora del equipo sean correctas.

### Actualizaciones firmadas (gratis)

Cada versión se firma con una clave privada (Ed25519) que solo tienes tú; la app lleva
la clave pública y **rechaza cualquier actualización sin firma o con firma incorrecta**,
aunque alguien consiga escribir en la carpeta de red o publicar en GitHub.

1. **Una sola vez**, en el PC desde el que publicas: `py -m pip install cryptography` y
   `py generar_claves.py`. Guarda la clave privada (`~/.accesos-directos-firma/clave_privada.pem`)
   con copia de seguridad y **no la subas a GitHub ni a la carpeta de red**. Si la pierdes,
   las apps instaladas no aceptarán más actualizaciones.
2. Pega la línea `UPDATE_PUBLIC_KEY = "..."` que muestra en `app_config.py` y recompila.
3. Publica como siempre con `py publicar.py --build --notes "..."`: firma el `latest.json`
   y comprueba la firma antes de publicar. Con otra clave: `--clave RUTA.pem`.
4. **Si usas GitHub Releases** con la clave puesta, adjunta a la release los tres archivos que
   deja `publicar.py` en la carpeta de destino: `AccesosDirectos-X.zip`,
   `AccesosDirectos-X-source.zip` y `latest.json` (este último con ese nombre exacto).

Limitación: la primera instalación con `instalar.bat` confía en la carpeta de red; la firma
se comprueba en todas las actualizaciones posteriores.

### Seguridad (importante)

Quien pueda escribir en la carpeta/servidor de actualizaciones puede ejecutar código en
todos los PCs. Da permiso de **escritura solo a quien publica** y **solo lectura** al resto.
El SHA-256 protege contra descargas dañadas; lo que protege contra alguien con acceso de
escritura es la **firma** (ver arriba). Activa además la verificación en dos pasos (2FA) en
tu cuenta de GitHub.

## Desarrollo en Linux

```
pip install -r requirements.txt
python3 main.py
```
`win_icons.py` se desactiva solo fuera de Windows (verás emojis en vez de iconos reales).
