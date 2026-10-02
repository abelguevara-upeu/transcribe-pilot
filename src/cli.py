#!/usr/bin/env python3
"""CLI de transcripcion. Pensado para puentes maquina-maquina.

  transcribe <archivo>...        transcribe y guarda en la BD
  transcribe list                lista el historial
  transcribe show <id>           imprime el texto de una fila
  transcribe redo <id>           vuelve a transcribir una fila
  transcribe rename <id> <txt>   cambia el titulo de una fila
  transcribe rm <id>...          elimina filas
  transcribe versions <id>       versiones anteriores de una fila
  transcribe log                 registro de cambios

Con --json la salida es JSON en stdout; los mensajes de progreso van a
stderr, para que stdout sea siempre parseable. Codigos de salida:
0 correcto, 1 error de uso, 2 fallo de transcripcion, 3 no encontrado.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
import db

EXIT_OK, EXIT_USAGE, EXIT_FAIL, EXIT_NOTFOUND = 0, 1, 2, 3


def log(msg, quiet=False):
    if not quiet:
        print(msg, file=sys.stderr)


def fila_publica(row, with_text=False):
    d = {
        "id": row["id"], "filename": row["filename"],
        "created_at": row["created_at"], "language": row["language"],
        "ext": row["ext"], "size_bytes": row["size_bytes"],
        "input_path": row["input_path"], "source": row["source"],
        "chars": len(row["text"] or ""),
    }
    if with_text:
        d["text"] = row["text"]
    return d


def cmd_transcribe(args):
    from together import Together

    try:
        client = Together(api_key=core.get_api_key())
    except core.TranscribeError as e:
        log(f"error: {e}", args.quiet)
        return EXIT_FAIL

    lang = args.language
    resultados, fallos = [], 0

    for path in args.files:
        log(f"-> {os.path.basename(path)}", args.quiet)
        try:
            row_id, text, reused = core.transcribe_file(
                path, client, language=lang, keep_in_place=args.in_place
            )
            resultados.append({
                "file": path, "id": row_id, "reused": reused,
                "chars": len(text), "ok": True,
            })
            estado = "ya existía" if reused else "transcrito"
            log(f"   {estado}: id={row_id} ({len(text)} chars)", args.quiet)
        except core.TranscribeError as e:
            fallos += 1
            resultados.append({"file": path, "ok": False, "error": str(e)})
            log(f"   error: {e}", args.quiet)

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
    return EXIT_FAIL if fallos else EXIT_OK


def cmd_list(args):
    rows = db.list_all(args.search or "")
    if args.json:
        print(json.dumps([fila_publica(r) for r in rows],
                         ensure_ascii=False, indent=2))
    else:
        for r in rows:
            lang = r["language"] or "?"
            print(f"{r['id']:4}  {r['created_at'][:16]}  {lang:3}  {r['filename']}")
        if not rows:
            print("(sin transcripciones)", file=sys.stderr)
    return EXIT_OK


def _get(row_id):
    for r in db.list_all():
        if r["id"] == row_id:
            return r
    return None


def cmd_show(args):
    row = _get(args.id)
    if not row:
        log(f"error: no existe la fila {args.id}", args.quiet)
        return EXIT_NOTFOUND
    if args.json:
        print(json.dumps(fila_publica(row, with_text=True),
                         ensure_ascii=False, indent=2))
    else:
        print(row["text"])
    return EXIT_OK


def cmd_redo(args):
    from together import Together

    row = _get(args.id)
    if not row:
        log(f"error: no existe la fila {args.id}", args.quiet)
        return EXIT_NOTFOUND
    try:
        client = Together(api_key=core.get_api_key())
        text = core.retranscribe_row(row, client, language=args.language)
    except core.TranscribeError as e:
        log(f"error: {e}", args.quiet)
        return EXIT_FAIL

    log(f"regenerado: id={row['id']} ({len(text)} chars)", args.quiet)
    if args.json:
        print(json.dumps({"id": row["id"], "chars": len(text), "ok": True},
                         ensure_ascii=False))
    return EXIT_OK


def cmd_rename(args):
    row = _get(args.id)
    if not row:
        log(f"error: no existe la fila {args.id}", args.quiet)
        return EXIT_NOTFOUND
    try:
        ruta, filename = core.renombrar_fila(row, args.title)
    except (core.TranscribeError, OSError) as e:
        log(f"error: {e}", args.quiet)
        return EXIT_FAIL

    log(f"renombrado: {os.path.splitext(filename)[0]}", args.quiet)
    if args.json:
        print(json.dumps({"id": row["id"], "filename": filename,
                          "input_path": ruta, "ok": True}, ensure_ascii=False))
    return EXIT_OK


def cmd_rm(args):
    borrados, faltan = [], []
    for rid in args.ids:
        row = _get(rid)
        if not row:
            faltan.append(rid)
            log(f"no existe la fila {rid}", args.quiet)
            continue
        removed = db.delete(rid, remove_files=args.files,
                            remove_original=args.original)
        borrados.append({"id": rid, "removed": removed})
        log(f"eliminado id={rid} ({len(removed)} archivo(s))", args.quiet)

    if args.json:
        print(json.dumps({"deleted": borrados, "not_found": faltan},
                         ensure_ascii=False, indent=2))
    return EXIT_NOTFOUND if faltan and not borrados else EXIT_OK


def cmd_versions(args):
    row = _get(args.id)
    if not row:
        log(f"error: no existe la fila {args.id}", args.quiet)
        return EXIT_NOTFOUND

    vs = db.versions(args.id)
    if args.restore:
        elegido = next((v for v in vs if v["id"] == args.restore), None)
        if not elegido:
            log(f"error: no existe la versión {args.restore}", args.quiet)
            return EXIT_NOTFOUND
        db.restore_version(args.restore)
        log(f"restaurada la versión {args.restore}", args.quiet)
        if args.json:
            print(json.dumps({"id": args.id, "restored": args.restore,
                              "ok": True}, ensure_ascii=False))
        return EXIT_OK

    if args.json:
        print(json.dumps(vs, ensure_ascii=False, indent=2))
        return EXIT_OK

    print(f"actual    {row['created_at'][:16]}  [{row['language'] or '?'}]"
          f"  {len(row['text'] or '')} chars")
    for v in vs:
        print(f"v{v['id']:<8} {v['created_at'][:16]}  [{v['language'] or '?'}]"
              f"  {len(v['text'])} chars  (reemplazada {v['replaced_at'][:16]})")
    if not vs:
        print("(sin versiones anteriores)", file=sys.stderr)
    return EXIT_OK


def cmd_log(args):
    filas = db.audit(limit=args.limit, row_id=args.id, action=args.action)
    if args.json:
        print(json.dumps(filas, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not filas:
        print("(sin registros)", file=sys.stderr)
        return EXIT_OK
    for a in filas:
        cuando = a["at"].replace("T", " ")
        quien = f"{a['actor'] or '?'}@{a['origin'] or '?'}"
        sujeto = a["subject"] or ""
        print(f"{cuando}  {a['action']:13} {quien:22} "
              f"id={str(a['row_id'] or '-'):4} {sujeto}")
        if a["detail"]:
            print(f"{'':22}{a['detail']}")
    return EXIT_OK


def build_parser():
    p = argparse.ArgumentParser(
        prog="transcribe", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true", help="salida JSON en stdout")
    p.add_argument("-q", "--quiet", action="store_true", help="sin mensajes de progreso")
    sub = p.add_subparsers(dest="cmd")

    idiomas = [v for v in core.IDIOMAS.values() if v]

    t = sub.add_parser("run", help="transcribe uno o varios archivos")
    t.add_argument("files", nargs="+")
    t.add_argument("-l", "--language", choices=idiomas, default=None,
                   help="idioma; por defecto lo detecta Whisper")
    t.add_argument("--in-place", action="store_true",
                   help="no copiar a in/, transcribir donde está")
    t.set_defaults(func=cmd_transcribe)

    l = sub.add_parser("list", help="lista el historial")
    l.add_argument("-s", "--search", help="filtra por nombre o contenido")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="imprime el texto de una fila")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_show)

    r = sub.add_parser("redo", help="vuelve a transcribir una fila")
    r.add_argument("id", type=int)
    r.add_argument("-l", "--language", choices=idiomas, default=None)
    r.set_defaults(func=cmd_redo)

    v = sub.add_parser("versions", help="versiones anteriores de una fila")
    v.add_argument("id", type=int)
    v.add_argument("--restore", type=int, metavar="VER",
                   help="restaura esa versión (la actual se archiva)")
    v.set_defaults(func=cmd_versions)

    g = sub.add_parser("log", help="registro de cambios")
    g.add_argument("-n", "--limit", type=int, default=30)
    g.add_argument("--id", type=int, help="solo los cambios de esa fila")
    g.add_argument("--action", help="crear, renombrar, retranscribir, convertir, eliminar")
    g.set_defaults(func=cmd_log)

    mv = sub.add_parser("rename", help="cambia el titulo de una fila")
    mv.add_argument("id", type=int)
    mv.add_argument("title", help="nuevo título; la fecha y el tipo se conservan")
    mv.set_defaults(func=cmd_rename)

    d = sub.add_parser("rm", help="elimina filas")
    d.add_argument("ids", nargs="+", type=int)
    d.add_argument("--files", action="store_true", help="borra tambien .txt y .wav")
    d.add_argument("--original", action="store_true",
                   help="borra tambien el original de in/ (implica --files)")
    d.set_defaults(func=cmd_rm)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    if getattr(args, "original", False):
        args.files = True
    db.init()
    db.set_origin("cli")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
