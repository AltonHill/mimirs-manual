#!/usr/bin/env python3
"""build-briefs.py — regenerate the customer design brief's derived formats.

The Markdown is the master. This script rebuilds everything derived from
it, so a correction to the .md never orphans the other formats again
(Brief M6, ruled "keep the promises"):

  1. diagrams/architecture.png and diagrams/cable-diagram.png from their
     SVGs (rsvg-convert, 2x scale) — the .docx embeds architecture.png;
  2. customer-design-brief.docx from customer-design-brief.md — written
     directly as Office Open XML with the Python standard library (no
     python-docx), so the build has no pip dependency;
  3. customer-design-brief.pdf from that .docx — LibreOffice headless
     (soffice), run with TZ=UTC and a throwaway profile;
  4. neutral metadata in both: author "Project Cerberus", no last-
     modified-by, dates fixed to the brief's own date at 00:00 UTC (no
     time-of-day or timezone tells), via qpdf: the PDF Info dictionary
     is rewritten, and the XMP metadata stream (which LibreOffice fills
     with the real build time — xmp:CreateDate/ModifyDate, dc:date) is
     stripped, so no real build date survives anywhere in the PDF;
  5. --verify (also run after every build): every paragraph, list item
     and table cell of the .md must appear in the .docx text and in the
     .pdf text (pdftotext), whitespace-normalized. Exit 1 on any miss.

Usage (from anywhere):
    python3 proxmox-ceph/docs/build-briefs.py            # build + verify
    python3 proxmox-ceph/docs/build-briefs.py --verify   # verify only

Needs: python3 (3.8+), rsvg-convert, soffice (LibreOffice), qpdf,
pdftotext (poppler). The customer-protection-brief.md has no derived
formats — it is Markdown only.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(HERE, "customer-design-brief.md")
DOCX = os.path.join(HERE, "customer-design-brief.docx")
PDF = os.path.join(HERE, "customer-design-brief.pdf")
DIAGRAMS = os.path.join(HERE, "diagrams")
SVG_PNG = (("architecture.svg", "architecture.png"),
           ("cable-diagram.svg", "cable-diagram.png"))
AUTHOR = "Project Cerberus"
TITLE = "Customer Design Brief"

# ---------------------------------------------------------------- markdown


def parse_md(text):
    """Blocks: (kind, payload). kinds: title, h2, h3, para, meta, bullets,
    numbers, table, image, rule, center."""
    lines = text.splitlines()
    blocks, i = [], 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("# "):
            blocks.append(("title", s[2:].strip()))
        elif s.startswith("### "):
            blocks.append(("h3", s[4:].strip()))
        elif s.startswith("## "):
            blocks.append(("h2", s[3:].strip()))
        elif s == "---":
            blocks.append(("rule", ""))
        elif re.match(r"^\[.*\]$", s):
            blocks.append(("center", s))
        elif s.startswith("!["):
            m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", s)
            blocks.append(("image", (m.group(1), m.group(2))))
        elif s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.match(r"^:?-+:?$", c) for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue
        elif re.match(r"^\*\*[^*]+:\*\*", s) and blocks and blocks[-1][0] in ("meta", "center", "title"):
            meta = []
            while i < len(lines) and re.match(r"^\*\*[^*]+:\*\*", lines[i].strip()):
                m = re.match(r"^\*\*([^*]+):\*\*\s*(.*)$", lines[i].strip())
                meta.append((m.group(1), m.group(2)))
                i += 1
            blocks.append(("meta", meta))
            continue
        elif re.match(r"^[-*] ", s) or re.match(r"^\d+\. ", s):
            kind = "bullets" if re.match(r"^[-*] ", s) else "numbers"
            items = []
            while i < len(lines):
                t = lines[i]
                if re.match(r"^[-*] ", t.strip()) or re.match(r"^\d+\. ", t.strip()):
                    if (kind == "bullets") != bool(re.match(r"^[-*] ", t.strip())):
                        break
                    items.append(re.sub(r"^([-*]|\d+\.)\s+", "", t.strip()))
                elif t.startswith("  ") and t.strip() and items:
                    items[-1] += " " + t.strip()
                else:
                    break
                i += 1
            blocks.append((kind, items))
            continue
        else:
            para = [s]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(
                    r"^(#|\||!\[|---$|[-*] |\d+\. )", lines[i].strip()):
                para.append(lines[i].strip())
                i += 1
            blocks.append(("para", " ".join(para)))
            continue
        i += 1
    return blocks


def plain(md):
    """Inline Markdown -> plain text (for verification)."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", md)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t

# ---------------------------------------------------------------- docx XML


W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def runs(md, size=None, color=None, bold=False, italic=False):
    """Inline Markdown -> <w:r> runs (**bold**, *italic*, `code`)."""
    out = []
    for tok in re.split(r"(\*\*.+?\*\*|(?<![\w*])\*(?!\s).+?(?<!\s)\*(?![\w*])|`[^`]+`)", md):
        if not tok:
            continue
        b, it, mono, txt = bold, italic, False, tok
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            b, txt = True, tok[2:-2]
        elif tok.startswith("`") and tok.endswith("`"):
            mono, txt = True, tok[1:-1]
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            it, txt = True, tok[1:-1]
        rpr = ""
        if mono:
            rpr += '<w:rFonts w:ascii="Liberation Mono" w:hAnsi="Liberation Mono" w:cs="Liberation Mono"/>'
        if b:
            rpr += "<w:b/>"
        if it:
            rpr += "<w:i/>"
        if color:
            rpr += f'<w:color w:val="{color}"/>'
        if size:
            rpr += f'<w:sz w:val="{size}"/>'
        out.append(f'<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}'
                   f'<w:t xml:space="preserve">{escape(txt)}</w:t></w:r>')
    return "".join(out)


def para(content, style=None, jc=None, num=None, spacing_after=None):
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if num:
        ppr += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/></w:numPr>'
    if spacing_after is not None:
        ppr += f'<w:spacing w:after="{spacing_after}"/>'
    if jc:
        ppr += f'<w:jc w:val="{jc}"/>'
    return f'<w:p>{"<w:pPr>" + ppr + "</w:pPr>" if ppr else ""}{content}</w:p>'


def table(rows, header=True, widths=None):
    ncol = max(len(r) for r in rows)
    total = 9360
    widths = widths or [total // ncol] * ncol
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    trs = []
    for ri, row in enumerate(rows):
        tcs = []
        for ci in range(ncol):
            cell = row[ci] if ci < len(row) else ""
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="E8EEF5"/>' if header and ri == 0 else ""
            tcs.append(f'<w:tc><w:tcPr><w:tcW w:w="{widths[ci]}" w:type="dxa"/>{shade}</w:tcPr>'
                       f'{para(runs(cell, size=19, bold=header and ri == 0), spacing_after=40)}</w:tc>')
        trs.append(f'<w:tr>{"<w:trPr><w:tblHeader/></w:trPr>" if header and ri == 0 else ""}'
                   f'{"".join(tcs)}</w:tr>')
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" '
            'w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{"".join(trs)}</w:tbl>'
            + para("", spacing_after=60))


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


def image(rid, path, name, max_width_in=6.3):
    w, h = png_size(path)
    cx = int(max_width_in * 914400)
    cy = int(cx * h / w)
    return para(
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="{escape(name)}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="{escape(os.path.basename(path))}"/>'
        '<pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>', jc="center")


STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans" w:cs="Liberation Sans" w:eastAsia="Liberation Sans"/><w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="240"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3B5B"/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="320" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3B5B"/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="2E5A88"/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:i/><w:color w:val="4B5563"/><w:sz w:val="19"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="60"/><w:ind w:left="720"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:color w:val="6B7280"/><w:sz w:val="16"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="BFC7D1"/><w:left w:val="single" w:sz="4" w:space="0" w:color="BFC7D1"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="BFC7D1"/><w:right w:val="single" w:sz="4" w:space="0" w:color="BFC7D1"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="BFC7D1"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="BFC7D1"/></w:tblBorders><w:tblCellMar><w:left w:w="90" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""

NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
{nums}
</w:numbering>"""


def build_docx(md_text, date_iso):
    blocks = parse_md(md_text)
    body, media, num_lists = [], [], []
    for kind, val in blocks:
        if kind == "title":
            body.append(para(runs(val), style="Title"))
        elif kind == "center":
            body.append(para(runs(val, size=22, color="6B7280"), jc="center"))
        elif kind == "meta":
            body.append(table([[f"**{k}**", v] for k, v in val], header=False,
                              widths=[2600, 6760]))
        elif kind == "h2":
            body.append(para(runs(val), style="Heading1"))
        elif kind == "h3":
            body.append(para(runs(val), style="Heading2"))
        elif kind == "rule":
            body.append(para('', spacing_after=60))
        elif kind == "para":
            if val.startswith("*") and val.endswith("*") and not val.startswith("**"):
                body.append(para(runs(val[1:-1], italic=True), style="Caption"))
            else:
                body.append(para(runs(val)))
        elif kind == "bullets":
            body += [para(runs(it), style="ListParagraph", num=1) for it in val]
        elif kind == "numbers":
            nid = 2 + len(num_lists)
            num_lists.append(nid)
            body += [para(runs(it), style="ListParagraph", num=nid) for it in val]
        elif kind == "table":
            body.append(table(val))
        elif kind == "image":
            alt, rel = val
            path = os.path.normpath(os.path.join(HERE, rel))
            rid = f"rIdImg{len(media) + 1}"
            media.append((rid, path))
            body.append(image(rid, path, alt))
    sect = ('<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
            '<w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1270" w:right="1270" w:bottom="1270" w:left="1270" '
            'w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document {W_NS}><w:body>{"".join(body)}{sect}</w:body></w:document>')
    footer = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr>'
              '<w:r><w:t xml:space="preserve">Project Cerberus — Customer Design Brief · page </w:t></w:r>'
              '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
              '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
              '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
    nums = "".join(f'<w:num w:numId="{n}"><w:abstractNumId w:val="1"/>'
                   f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
                   for n in num_lists)
    rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
            '<Relationship Id="rIdNum" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>',
            '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>',
            '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>']
    for rid, path in media:
        rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
                    f'officeDocument/2006/relationships/image" Target="media/{os.path.basename(path)}"/>')
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            '</Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            '</Relationships>',
        "docProps/core.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(TITLE)}</dc:title><dc:creator>{AUTHOR}</dc:creator>'
            '<cp:lastModifiedBy></cp:lastModifiedBy><cp:revision>1</cp:revision>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{date_iso}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{date_iso}</dcterms:modified>'
            '</cp:coreProperties>',
        "docProps/app.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            '<Application>build-briefs.py</Application></Properties>',
        "word/document.xml": document,
        "word/styles.xml": STYLES,
        "word/numbering.xml": NUMBERING.replace("{nums}", nums),
        "word/settings.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:defaultTabStop w:val="720"/><w:compat><w:compatSetting w:name="compatibilityMode" '
            'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>',
        "word/footer1.xml": footer,
        "word/_rels/document.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels) + "</Relationships>",
    }
    stamp = tuple(int(x) for x in re.findall(r"\d+", date_iso)[:6])
    tmp = DOCX + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):          # deterministic: fixed order + fixed stamps
            zi = zipfile.ZipInfo(name, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[name].encode("utf-8"))
        for rid, path in media:
            zi = zipfile.ZipInfo(f"word/media/{os.path.basename(path)}", date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as f:
                z.writestr(zi, f.read())
    os.replace(tmp, DOCX)
    print(f"wrote {os.path.relpath(DOCX)} ({len(blocks)} blocks, {len(media)} image(s))")

# ---------------------------------------------------------------- steps


def need(tool):
    if not shutil.which(tool):
        sys.exit(f"build-briefs: `{tool}` not found on PATH (see the header for requirements)")


def build_pngs():
    need("rsvg-convert")
    for svg, png in SVG_PNG:
        src, dst = os.path.join(DIAGRAMS, svg), os.path.join(DIAGRAMS, png)
        subprocess.run(["rsvg-convert", "-z", "2", "-b", "white", src, "-o", dst], check=True)
        w, h = png_size(dst)
        print(f"wrote {os.path.relpath(dst)} ({w}x{h})")


def build_pdf(date_pdf):
    need("soffice")
    need("qpdf")
    with tempfile.TemporaryDirectory(prefix="build-briefs-") as tmp:
        env = dict(os.environ, TZ="UTC", LC_ALL="C.UTF-8")
        profile = "file://" + os.path.join(tmp, "profile")
        subprocess.run(["soffice", f"-env:UserInstallation={profile}", "--headless",
                        "--convert-to", "pdf", "--outdir", tmp, DOCX],
                       check=True, env=env, stdout=subprocess.DEVNULL)
        raw = os.path.join(tmp, os.path.basename(PDF))
        # Neutral Info dictionary: author, fixed UTC dates (no tz / time tells).
        js = os.path.join(tmp, "pdf.json")
        subprocess.run(["qpdf", "--json-output", raw, js], check=True)
        doc = json.load(open(js))
        objs = doc["qpdf"][1]
        info_ref = objs["trailer"]["value"]["/Info"]
        info = objs[f"obj:{info_ref}"]["value"]
        info["/Author"] = f"u:{AUTHOR}"
        info["/Title"] = f"u:{TITLE}"
        info["/CreationDate"] = f"u:{date_pdf}"
        info["/ModDate"] = f"u:{date_pdf}"
        upd = {"qpdf": [doc["qpdf"][0], {f"obj:{info_ref}": {"value": info}}]}
        json.dump(upd, open(js, "w"))
        # --remove-metadata strips the XMP stream, which LibreOffice
        # stamps with the real build time; the Info dictionary survives,
        # so the file carries only the fixed dates written above.
        subprocess.run(["qpdf", raw, "--remove-metadata", f"--update-from-json={js}",
                        "--deterministic-id", PDF],
                       check=True)
    print(f"wrote {os.path.relpath(PDF)}")


def norm(s):
    s = s.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    s = re.sub(r"[‐-‒]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        x = z.read("word/document.xml").decode("utf-8")
    x = re.sub(r"</w:p>", "\n", x)
    x = re.sub(r"</w:tc>", " \n", x)
    x = re.sub(r"<[^>]+>", "", x)
    return (x.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'"))


def verify():
    need("pdftotext")
    md = open(MD, encoding="utf-8").read()
    want = []
    for kind, val in parse_md(md):
        if kind in ("title", "h2", "h3", "para", "center"):
            caption = kind == "para" and val.startswith("*") and not val.startswith("**")
            want.append(plain(val[1:-1] if caption else val))
        elif kind in ("bullets", "numbers"):
            want += [plain(v) for v in val]
        elif kind == "table":
            want += [plain(c) for row in val for c in row if c]
        elif kind == "meta":
            want += [f"{k}" for k, v in val] + [plain(v) for k, v in val]
    dtext = norm(docx_text(DOCX))
    ptext = norm(subprocess.run(["pdftotext", "-raw", PDF, "-"], check=True,
                                capture_output=True, text=True).stdout)
    pflat = re.sub(r"[\s-]", "", ptext)
    miss_d = [w for w in want if norm(w) not in dtext]
    miss_p = [w for w in want if re.sub(r"[\s-]", "", norm(w)) not in pflat]
    for w in miss_d:
        print(f"VERIFY-MISS docx: {w[:100]}")
    for w in miss_p:
        print(f"VERIFY-MISS pdf : {w[:100]}")
    meta = subprocess.run(["pdfinfo", "-rawdates", PDF], capture_output=True, text=True).stdout \
        if shutil.which("pdfinfo") else ""
    print(f"verify: {len(want)} text units from the .md; docx misses {len(miss_d)}, "
          f"pdf misses {len(miss_p)}")
    for line in meta.splitlines():
        if line.split(":")[0] in ("Author", "Title", "Creator", "Producer", "CreationDate", "ModDate", "Pages"):
            print(f"  pdf {line}")
    return 1 if (miss_d or miss_p) else 0


def brief_date(md_text):
    m = re.search(r"\*\*Date:\*\*\s*([A-Za-z]+ \d{1,2}, \d{4})", md_text)
    d = datetime.datetime.strptime(m.group(1), "%B %d, %Y") if m else datetime.datetime(2026, 1, 1)
    return d.strftime("%Y-%m-%dT00:00:00Z"), d.strftime("D:%Y%m%d000000Z")


def main():
    ap = argparse.ArgumentParser(description="Rebuild the design brief's .docx/.pdf (+ diagram PNGs) from the .md")
    ap.add_argument("--verify", action="store_true", help="only verify the three formats agree")
    a = ap.parse_args()
    if not a.verify:
        md_text = open(MD, encoding="utf-8").read()
        iso, pdfdate = brief_date(md_text)
        build_pngs()
        build_docx(md_text, iso)
        build_pdf(pdfdate)
    return verify()


if __name__ == "__main__":
    sys.exit(main())
