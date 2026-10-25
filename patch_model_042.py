#!/usr/bin/env python
"""TE-Speed-MiniMaxH3-OSS hook patcher, adapted for ComfyUI 0.42's
comfy/ldm/minimax/model.py.

The upstream patch_model.py (from HELPMEEADICE/TE-Speed-MiniMaxH3-OSS) fails on
ComfyUI 0.42 with:
    error: could not find the stock block loop in the model file.

because 0.42 rewrote the MiniMax H3 block loop (new prefetch API with
malloc_scope, transformer_options["block_index"], attention passthrough, layout
in double_block hook args). This patcher adapts to that structure.

Design: the stock block-loop text is EXTRACTED from the live model.py and its
anchors verified character-for-character (any mismatch aborts without writing).
The else-branch and the _run_blocks body are then built from that verified text
via mechanical transforms (block-range slicing, device -> h.device, global
block_index, layout passthrough) - no hand-retyped code.

Usage:
    python patch_model_042.py                          # auto-locate and patch
    python patch_model_042.py --comfy-ui /opt/ComfyUI  # explicit ComfyUI root
    python patch_model_042.py --check                  # only report status
    python patch_model_042.py --revert                 # restore the .bak backup

A backup is written next to the file (model.py.te_speed.bak) before patching.
"""
import argparse
import ast
from pathlib import Path

FWD_ANCHOR = "    def forward(self, x, timestep, context,"

START = (
    '        # blocks\n'
    '        patches_replace = transformer_options.get("patches_replace", {})\n'
    '        blocks_replace = patches_replace.get("dit", {})\n'
    '        prefetch_queue = comfy.model_prefetch.make_prefetch_queue(list(self.blocks), device, transformer_options)\n'
)
END = '        comfy.model_prefetch.prefetch_queue_pop(prefetch_queue, device, None, malloc_scope="block")\n'

COMMON_ROOTS = [
    Path("/opt/ComfyUI"),
    Path("."),
    Path(".."),
    Path("ComfyUI"),
    Path("../ComfyUI"),
    Path("C:/ComfyUI"),
    Path("D:/ComfyUI"),
]


def find_model_file(comfy_ui):
    if comfy_ui:
        roots = [Path(comfy_ui)]
    else:
        roots = COMMON_ROOTS
    for root in roots:
        for cand in (root / "comfy/ldm/minimax/model.py", root / "ComfyUI/comfy/ldm/minimax/model.py"):
            if cand.is_file():
                return cand
    raise SystemExit("error: could not locate comfy/ldm/minimax/model.py - pass --comfy-ui <ComfyUI root>")


def hook_status(text):
    return ('def _run_blocks' in text) and ('("block_loop", 0) in blocks_replace' in text)


def build_patch(text):
    if text.count(START) != 1:
        raise SystemExit("error: stock block loop start anchor not unique/found (%d) - "
                         "your ComfyUI version may differ from 0.42" % text.count(START))
    i0 = text.index(START)
    i1 = text.find(END, i0)
    if i1 < 0:
        raise SystemExit("error: stock block loop end anchor not found - "
                         "your ComfyUI version may differ from 0.42")
    i1 += len(END)
    stock = text[i0:i1]

    lines = stock.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    assign_lines = lines[1:3]        # patches_replace / blocks_replace assignments
    loop_lines = lines[3:]           # from prefetch_queue creation to the final pop

    # else-branch: the verified stock loop, re-indented +4
    else_body = "\n".join(("    " + l) if l.strip() else l for l in loop_lines)

    # _run_blocks body: mechanical edits of the verified stock loop
    rb = "\n".join(loop_lines)
    rb = rb.replace("make_prefetch_queue(list(self.blocks), device, transformer_options)",
                    "make_prefetch_queue(list(self.blocks[start:end]), h.device, transformer_options)")
    rb = rb.replace("for i, block in enumerate(self.blocks):",
                    "for i in range(start, end):\n            block = self.blocks[i]")
    rb = rb.replace("prefetch_queue_pop(prefetch_queue, device, block,",
                    "prefetch_queue_pop(prefetch_queue, h.device, block,")
    rb = rb.replace("prefetch_queue_pop(prefetch_queue, device, None,",
                    "prefetch_queue_pop(prefetch_queue, h.device, None,")
    if "h.device" not in rb or "range(start, end)" not in rb:
        raise SystemExit("error: mechanical edits did not apply - stock text shape unexpected")
    rb = rb.rstrip("\n") + "\n        return h"

    run_blocks = (
        "    def _run_blocks(self, h, t_emb, mod_segments, rope_freqs, transformer_options, layout=None, start=0, end=None):\n"
        "        patches_replace = transformer_options.get(\"patches_replace\", {})\n"
        "        blocks_replace = patches_replace.get(\"dit\", {})\n"
        "        end = len(self.blocks) if end is None else end\n"
        + rb + "\n\n"
    )

    hook_branch = (
        '        if ("block_loop", 0) in blocks_replace:\n'
        '            cache_ranges = [(a, b) for a, b, kind in layout.segments if kind in ("audio", "video")]\n'
        '            def block_loop_wrap(args):\n'
        '                return {"img": self._run_blocks(args["img"], args["t_emb"], args["mod_segments"], args["rope_freqs"],\n'
        '                                                args["transformer_options"], args.get("layout"), args.get("start", 0), args.get("end"))}\n'
        '            h = blocks_replace[("block_loop", 0)](\n'
        '                {"img": h, "t_emb": t_emb, "mod_segments": mod_segments, "rope_freqs": rope_freqs,\n'
        '                 "layout": layout, "transformer_options": transformer_options,\n'
        '                 "cache_ranges": cache_ranges, "block_count": len(self.blocks)},\n'
        '                {"original_block": block_loop_wrap})["img"]\n'
        '        else:\n'
    )

    new_block = (
        "        # blocks (TE-Speed-MiniMaxH3-OSS hook, adapted for 0.42)\n"
        + "\n".join(assign_lines) + "\n"
        + hook_branch
        + else_body + "\n"
    )

    patched = text[:i0] + new_block + text[i1:]

    fwd = patched.rfind(FWD_ANCHOR, 0, patched.index(new_block))
    if fwd < 0:
        raise SystemExit("error: forward anchor not found above the block loop")
    patched = patched[:fwd] + run_blocks + patched[fwd:]
    return patched


def main():
    ap = argparse.ArgumentParser(description="Install MiniMax H3 block_loop hooks (ComfyUI 0.42 adaptation)")
    ap.add_argument("--comfy-ui", help="ComfyUI root directory (auto-detected when omitted)")
    ap.add_argument("--revert", action="store_true", help="restore the pre-patch backup")
    ap.add_argument("--check", action="store_true", help="only report hook status")
    args = ap.parse_args()

    target = find_model_file(args.comfy_ui)
    backup = target.with_name(target.name + ".te_speed.bak")
    text = target.read_text(encoding="utf-8")

    if args.revert:
        if not backup.is_file():
            raise SystemExit("error: no backup at %s" % backup)
        target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        print("restored %s from backup" % target)
        return

    if hook_status(text):
        print("hooks already present in %s" % target)
        return

    if args.check:
        print("hooks MISSING in %s" % target)
        return

    if backup.is_file():
        if backup.read_text(encoding="utf-8") != text:
            raise SystemExit("error: backup exists but differs from current file - aborting")
    else:
        backup.write_text(text, encoding="utf-8")
        print("backup written to %s" % backup)

    patched = build_patch(text)
    ast.parse(patched)  # syntax gate
    assert hook_status(patched)
    target.write_text(patched, encoding="utf-8")
    print("patched OK: block_loop hook + _run_blocks installed (%s)" % target)
    print("restart ComfyUI, then use the TESpeedMiniMaxH3 node. Revert any time with --revert.")


if __name__ == "__main__":
    main()
