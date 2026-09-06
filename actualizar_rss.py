from __future__ import annotations

import copy
import email.utils
import html
import os
import re
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


BASE = "https://www.betaville.co.uk"
PORTADA = f"{BASE}/"

SALIDA = Path("rss.xml")
MAXIMO_ARTICULOS = 1000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
)

CABECERAS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
}


def descargar(url: str) -> bytes:
    peticion = urllib.request.Request(
        url,
        headers=CABECERAS,
    )

    with urllib.request.urlopen(
        peticion,
        timeout=120,
    ) as respuesta:
        contenido = respuesta.read()

    if not contenido:
        raise RuntimeError("Betaville devolvió una página vacía")

    return contenido


def limpiar_url(url: str) -> str:
    url = urljoin(BASE, url.strip())
    return url.split("#", 1)[0].split("?", 1)[0].rstrip("/")


def es_articulo(url: str) -> bool:
    return (
        url.startswith(f"{BASE}/news/")
        or url.startswith(
            f"{BASE}/betaville-intelligence/"
        )
        or url.startswith(f"{BASE}/exclusive/")
    )


def convertir_fecha(fecha: str) -> datetime:
    fecha = " ".join(fecha.split())

    formatos = (
        "%A, %d %B %Y %I:%M %p",
        "%A, %d %B %Y %H:%M",
        "%d %B %Y %I:%M %p",
        "%d %B %Y %H:%M",
    )

    for formato in formatos:
        try:
            resultado = datetime.strptime(
                fecha,
                formato,
            )

            return resultado.replace(
                tzinfo=ZoneInfo("Europe/London")
            )

        except ValueError:
            continue

    return datetime.now(timezone.utc)


def texto_limpio(texto: str) -> str:
    return html.unescape(
        " ".join(texto.split())
    ).strip()


def buscar_bloques(
    sopa: BeautifulSoup,
) -> list:
    bloques = []
    vistos: set[int] = set()

    for enlace in sopa.select(
        'h2 a[href^="/news/"], '
        'h2 a[href^="/betaville-intelligence/"], '
        'h2 a[href^="/exclusive/"]'
    ):
        bloque = enlace.find_parent(
            "div",
            class_=lambda clases: (
                clases
                and any(
                    "__post" in clase
                    for clase in (
                        clases
                        if isinstance(clases, list)
                        else [clases]
                    )
                )
            ),
        )

        if bloque is None:
            bloque = enlace.parent.parent

        identificador = id(bloque)

        if identificador not in vistos:
            bloques.append(bloque)
            vistos.add(identificador)

    return bloques


def extraer_resumen(bloque) -> str:
    selectores = (
        ".fwx-copy-public",
        ".fwx-copy",
        'span[class*="copy-public"]',
        'div[class*="intro"]',
    )

    for selector in selectores:
        nodo = bloque.select_one(selector)

        if nodo:
            resumen = texto_limpio(
                nodo.get_text(" ", strip=True)
            )

            if resumen:
                return resumen

    return ""


def extraer_fecha(bloque) -> str:
    fecha_nodo = bloque.select_one(
        'span[class*="__date"]'
    )

    if fecha_nodo:
        return texto_limpio(
            fecha_nodo.get_text(
                " ",
                strip=True,
            )
        )

    return ""


def extraer_etiquetas(bloque) -> list[str]:
    etiquetas: list[str] = []
    vistas: set[str] = set()

    for enlace in bloque.select('a[href^="/tags/"]'):
        etiqueta = texto_limpio(
            enlace.get_text(
                " ",
                strip=True,
            )
        )

        clave = etiqueta.casefold()

        if etiqueta and clave not in vistas:
            etiquetas.append(etiqueta)
            vistas.add(clave)

    return etiquetas


def extraer_articulos() -> list[dict]:
    contenido = descargar(PORTADA)
    sopa = BeautifulSoup(contenido, "html.parser")

    articulos: list[dict] = []
    urls_vistas: set[str] = set()

    for bloque in buscar_bloques(sopa):
        enlace = bloque.select_one(
            'h2 a[href^="/news/"], '
            'h2 a[href^="/betaville-intelligence/"], '
            'h2 a[href^="/exclusive/"]'
        )

        if not enlace:
            continue

        url = limpiar_url(
            enlace.get("href", "")
        )

        if not es_articulo(url):
            continue

        if url in urls_vistas:
            continue

        titulo = texto_limpio(
            enlace.get_text(
                " ",
                strip=True,
            )
        )

        if not titulo:
            continue

        fecha_texto = extraer_fecha(bloque)
        resumen = extraer_resumen(bloque)
        etiquetas = extraer_etiquetas(bloque)

        if "/betaville-intelligence/" in url:
            tipo = "Betaville Intelligence"
        elif "/exclusive/" in url:
            tipo = "Exclusive"
        else:
            tipo = "News"

        articulos.append(
            {
                "url": url,
                "titulo": titulo,
                "fecha_texto": fecha_texto,
                "fecha": convertir_fecha(fecha_texto),
                "resumen": resumen,
                "etiquetas": etiquetas,
                "tipo": tipo,
            }
        )

        urls_vistas.add(url)

    if not articulos:
        raise RuntimeError(
            "No se encontraron artículos. "
            "Betaville puede haber cambiado su diseño."
        )

    return articulos


def texto_elemento(
    elemento: ET.Element,
    nombre: str,
) -> str:
    nodo = elemento.find(nombre)

    if nodo is None or nodo.text is None:
        return ""

    return nodo.text.strip()


def cargar_rss_anterior() -> dict[str, ET.Element]:
    articulos: dict[str, ET.Element] = {}

    if not SALIDA.exists():
        return articulos

    try:
        raiz = ET.parse(SALIDA).getroot()
        canal = raiz.find("channel")

        if canal is None:
            return articulos

        for item in canal.findall("item"):
            guid = (
                texto_elemento(item, "guid")
                or texto_elemento(item, "link")
            )

            if guid:
                articulos[guid.rstrip("/")] = copy.deepcopy(
                    item
                )

    except ET.ParseError:
        print(
            "El rss.xml anterior no era válido. "
            "Se reconstruirá."
        )

    return articulos


def crear_descripcion(datos: dict) -> str:
    partes: list[str] = []

    if datos["resumen"]:
        partes.append(
            f"<p>{html.escape(datos['resumen'])}</p>"
        )

    if datos["etiquetas"]:
        listado = ", ".join(
            html.escape(etiqueta)
            for etiqueta in datos["etiquetas"]
        )

        partes.append(
            "<p><strong>Empresas y etiquetas "
            f"relacionadas:</strong> {listado}</p>"
        )
    else:
        partes.append(
            "<p><strong>Empresas y etiquetas "
            "relacionadas:</strong> no indicadas.</p>"
        )

    partes.append(
        f"<p><strong>Sección:</strong> "
        f"{html.escape(datos['tipo'])}</p>"
    )

    partes.append(
        "<p>El artículo completo puede requerir "
        "una suscripción a Betaville.</p>"
    )

    return "".join(partes)


def crear_item(datos: dict) -> ET.Element:
    item = ET.Element("item")

    ET.SubElement(item, "title").text = datos["titulo"]
    ET.SubElement(item, "link").text = datos["url"]

    guid = ET.SubElement(
        item,
        "guid",
        {"isPermaLink": "true"},
    )
    guid.text = datos["url"]

    ET.SubElement(item, "pubDate").text = (
        email.utils.format_datetime(
            datos["fecha"]
        )
    )

    ET.SubElement(item, "description").text = (
        crear_descripcion(datos)
    )

    ET.SubElement(item, "category").text = datos["tipo"]

    for etiqueta in datos["etiquetas"]:
        ET.SubElement(item, "category").text = etiqueta

    ET.SubElement(item, "author").text = "Betaville"

    return item


def firma_item(item: ET.Element) -> tuple:
    return (
        texto_elemento(item, "title"),
        texto_elemento(item, "link"),
        texto_elemento(item, "pubDate"),
        texto_elemento(item, "description"),
        tuple(
            nodo.text or ""
            for nodo in item.findall("category")
        ),
    )


def fecha_item(item: ET.Element) -> datetime:
    fecha = texto_elemento(item, "pubDate")

    try:
        resultado = email.utils.parsedate_to_datetime(
            fecha
        )

        if resultado.tzinfo is None:
            resultado = resultado.replace(
                tzinfo=timezone.utc
            )

        return resultado

    except (TypeError, ValueError):
        return datetime.min.replace(
            tzinfo=timezone.utc
        )


def crear_rss(
    articulos: dict[str, ET.Element],
) -> ET.ElementTree:
    ordenados = sorted(
        articulos.values(),
        key=fecha_item,
        reverse=True,
    )[:MAXIMO_ARTICULOS]

    rss = ET.Element("rss", {"version": "2.0"})
    canal = ET.SubElement(rss, "channel")

    ET.SubElement(canal, "title").text = (
        "Betaville — News and affected companies"
    )

    ET.SubElement(canal, "link").text = BASE

    ET.SubElement(canal, "description").text = (
        "Betaville headlines, public summaries and "
        "lists of affected companies and related tags."
    )

    ET.SubElement(canal, "language").text = "en-GB"

    ET.SubElement(canal, "lastBuildDate").text = (
        email.utils.format_datetime(
            datetime.now(timezone.utc)
        )
    )

    for item in ordenados:
        canal.append(copy.deepcopy(item))

    return ET.ElementTree(rss)


def guardar_xml_atomico(
    arbol: ET.ElementTree,
) -> None:
    ET.indent(arbol, space="  ")

    with tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=".",
        prefix="rss_",
        suffix=".xml",
    ) as temporal:
        ruta_temporal = Path(temporal.name)

        arbol.write(
            temporal,
            encoding="utf-8",
            xml_declaration=True,
        )

    os.replace(ruta_temporal, SALIDA)


def main() -> None:
    descubiertos = extraer_articulos()
    guardados = cargar_rss_anterior()

    nuevos = 0
    modificados = 0

    for datos in descubiertos:
        nuevo_item = crear_item(datos)
        anterior = guardados.get(datos["url"])

        if anterior is None:
            guardados[datos["url"]] = nuevo_item
            nuevos += 1

        elif firma_item(anterior) != firma_item(nuevo_item):
            guardados[datos["url"]] = nuevo_item
            modificados += 1

    if nuevos == 0 and modificados == 0:
        print(
            "No hay publicaciones nuevas ni cambios "
            "en las empresas relacionadas"
        )
        return

    guardar_xml_atomico(
        crear_rss(guardados)
    )

    print(
        f"RSS actualizada. Nuevas: {nuevos}. "
        f"Modificadas: {modificados}. "
        f"Total conservadas: {len(guardados)}"
    )


if __name__ == "__main__":
    main()
