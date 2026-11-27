"""Static lexical analysis. Uploaded code is treated entirely as text."""

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from itertools import product
from dataclasses import dataclass
from statistics import pstdev

from .config import MAX_TOKENS

ANALYSIS_VERSION = "lexical-1"
SIMILARITY_VERSION = "tiling-2"
K = 6
SHORT_SAMPLE_TOKENS = 8
MAX_ANCHOR_COMBINATIONS = 48
MAX_LOCAL_GAP = 14
MIN_CONTAINMENT_TOKENS = 24
MIN_CONTAINMENT_BLOCK = 12
MAX_FRAGMENTS = 25

C_WORDS = frozenset("""
auto break case char const continue default do double else enum extern float for goto if int long
register return short signed sizeof static struct switch typedef union unsigned void volatile while
restrict inline _Alignas _Alignof _Atomic _Bool _Complex _Generic _Noreturn _Static_assert
_Thread_local alignas alignof bool constexpr false nullptr static_assert thread_local true typeof
include define undef ifdef ifndef elif endif pragma error defined
""".split())
MARIE_WORDS = frozenset("""
add addi subt load loadi loadimm store storei clear input output halt skipcond jump jumpi
jns adr dec oct hex bin org end
""".split())
ARM_WORDS = frozenset("""
adc add adr adrp and asr b bl blx br bx bic cbnz cbz ccmp ccmn cinc cinv cls clz cmn cmp cneg
csel cset csetm csinc csinv csneg cps dmb dsb eor eret extr fadd fcmp fdiv fmul fmov fsub
isb ldm ldmia ldmdb ldp ldpsw ldr ldrb ldrd ldrh ldrsb ldrsh ldrsw ldur ldurb ldaxr ldxr
lsl lsr madd mneg mov movk movn movt movw movz mrs msr msub mul mvn neg nop orr orn pop
push rbit ret rev rev16 rev32 ror rsb rsc sbc sdiv smaddl smull stlxr stp str strb strd strh
stur stxr sub svc swi sxtb sxth sxtw tbnz tbz teq tst udiv umaddl umull uxtb uxth wfe wfi udf
.word .long .short .hword .byte .quad .ascii .asciz .string .space .skip .fill .align .balign
.p2align .equ .set .global .globl .type .size .section .text .data .bss .rodata .thumb .thumb_func
.arm .arch .arch_extension .syntax .cpu .fpu .ltorg .include .macro .endm .end .if .endif
area dcd dcb dcw export import entry end equ preserve8 proc endp
""".split())
ARM_REGISTER = re.compile(
    r"(?:r(?:1[0-5]|[0-9])|[xw](?:[12][0-9]|30|[0-9]|zr|sp)|"
    r"[bhsdqvz](?:[12][0-9]|3[01]|[0-9])|p(?:1[0-5]|[0-9])|"
    r"sp|lr|pc|fp|ip|apsr|cpsr|spsr|nzcv)(?:\.(?:[1248]|16)?[bhsd])?"
)
ARM_CONDITION = re.compile(r"(?:\.?)(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)$")
VERILOG_WORDS = frozenset("""
always always_comb always_ff always_latch assign begin bit buf case casex casez default else end
endcase endfunction endgenerate endinterface endmodule endpackage endtask enum for foreach forever
fork function generate genvar if initial inout input int integer interface join join_any join_none
localparam logic module negedge or output package packed parameter posedge reg repeat return signed
struct task time typedef union unique unsigned wait while wire xor assertions assert property
endproperty sequence endsequence specify endspecify import export automatic begin end
`include `define `ifdef `ifndef `else `elsif `endif `timescale `undef
$display $write $finish $time $clog2 $signed $unsigned $fatal $error $readmemh $readmemb
""".split())
VHDL_WORDS = frozenset("""
abs access after alias all and architecture array assert attribute begin block body buffer bus
case component configuration constant context disconnect downto else elsif end entity exit file
for force function generate generic group guarded if impure in inertial inout is label library
linkage literal loop map mod nand new next nor not null of on open or others out package parameter
port postponed procedure process protected pure range record register reject release rem report
return rol ror select severity signal shared sla sll sra srl subtype then to transport type
unaffected units until use variable wait when while with xnor xor std_logic std_logic_vector
std_ulogic std_ulogic_vector signed unsigned integer natural positive boolean bit bit_vector
rising_edge falling_edge ieee std work std_logic_1164 numeric_std event stable
""".split())
CONTROL_WORDS = {
    "if", "elsif", "else", "switch", "case", "for", "while", "do", "return", "process",
    "always_ff", "always_comb", "always", "generate", "skipcond", "jns", "jump", "jumpi",
    "cbz", "cbnz", "b", "b.eq", "b.ne", "bl", "ret", "bx", "tbnz", "tbz",
}
KEYWORDS = {"c": C_WORDS, "marie": MARIE_WORDS, "arm": ARM_WORDS,
            "verilog": VERILOG_WORDS, "vhdl": VHDL_WORDS}
COMMENT_STYLES = {
    "none": r"(?!)",
    "c": r"/\*[\s\S]*?(?:\*/|\Z)|//[^\n]*",
    "marie": r"/[^\n]*",
    "arm": r"/\*[\s\S]*?(?:\*/|\Z)|//[^\n]*|[@;][^\n]*",
    "vhdl": r"--[^\n]*",
    "hash": r"\#[^\n]*",
    "semicolon": r";[^\n]*",
}
PROFILE_COMMENTS = {"c": "c", "marie": "marie", "arm": "arm", "verilog": "c", "vhdl": "vhdl"}
WORD = r"(?:`|\$)?[^\W\d]\w*"
NUMBER = r"\d[\d_]*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ?_]+| '[01xXzZ]|\d[\d_]*\#[0-9a-fA-F_.]+\#(?:[eE][+-]?\d+)?|0[xX][0-9a-fA-F_]+|0[bB][01_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?[uUlLfF]*"
STRING = r'"(?:\\[^\n]|[^"\\\n])*"|\'(?:\\[^\n]|[^\'\\\n])+\''
LABEL_FOLLOWS = re.compile(r'[ \t]*([,:])')
OPERATORS = r">>>|<<=|>>=|===|!==|\*\*|->|::|=>|:=|<=|>=|!=|==|\+\+|--|&&|\|\||<<|>>|[+*/%&|^\-]=|[^\s]"


@dataclass(frozen=True)
class Lexed:
    tokens: list[str]
    lines: list[int]
    comments: list[str]
    identifiers: set[str]
    control_count: int


@dataclass(frozen=True)
class Prepared:
    tokens: list[str]
    lines: list[int]
    grams: dict[tuple[str, ...], list[int]]
    normalized: tuple[str, ...]

    @classmethod
    def from_analysis(cls, stored: dict):
        tokens, lines = stored["tokens"], stored["lines"]
        grams = defaultdict(list)
        for i in range(max(0, len(tokens) - K + 1)):
            grams[canonical(tokens[i:i + K])].append(i)
        return cls(tokens, lines, grams, canonical(tokens))


def arm_keyword(word: str) -> bool:
    base = word.removesuffix(".w").removesuffix(".n")
    stripped = ARM_CONDITION.sub("", base)
    return base in ARM_WORDS or stripped in ARM_WORDS or stripped.removesuffix("s") in ARM_WORDS


def tokenize(source: str, profile: str, options: dict) -> Lexed:
    style = PROFILE_COMMENTS.get(profile, options.get("comments", "none"))
    insensitive = profile in {"marie", "vhdl"} or (
        profile == "generic" and options.get("case_insensitive", False)
    )
    keywords = KEYWORDS.get(profile, frozenset(options.get("keywords", [])))
    word_pattern = r"\.?" + WORD + r"(?:\.[a-zA-Z0-9]+)?" if profile == "arm" else WORD
    literal_pattern = STRING
    if profile == "vhdl":
        literal_pattern = r'(?:[boxBOX])?"(?:[^"\n]|"")*"|\'[^\n]\''
    numbers = NUMBER
    if profile == "marie":
        numbers = r"[0-9][0-9a-fA-F]*\b|" + numbers
    lexer = re.compile(
        rf"(?P<comment>{COMMENT_STYLES[style]})|(?P<string>{literal_pattern})"
        rf"|(?P<number>{numbers})|(?P<word>{word_pattern})|(?P<op>{OPERATORS})",
        re.VERBOSE,
    )
    tokens, lines, comments = [], [], []
    identifiers = set()
    control_count = 0
    line = 1
    previous_line = 0
    end = 0
    mnemonic = ""
    for match in lexer.finditer(source):
        line += source.count("\n", end, match.start())
        value = match.group()
        kind = match.lastgroup
        end = match.end()
        if kind == "comment":
            comments.append(value)
            line += value.count("\n")
            continue

        if profile in {"marie", "arm"} and line != previous_line:
            if previous_line:
                tokens.append("P:EOL")
                lines.append(previous_line)
            mnemonic = ""
        if kind == "word":
            word = value.lower() if insensitive else value
            following = LABEL_FOLLOWS.match(source, end)
            delimiter = following[1] if following else ""
            is_label = (
                (profile == "marie" and delimiter == ",")
                or (profile == "arm" and (delimiter == ":" or word.startswith(".L")))
            )
            if profile == "marie" and mnemonic == "skipcond" and word == "c00":
                token = "K:skipcond/c00"
            elif is_label:
                token = f"LBL:{word}"
                identifiers.add(word)
            elif profile == "marie" and mnemonic in {"hex", "bin", "org", "oct", "dec"} and re.fullmatch(r"[0-9a-f]+", word):
                token = "L:number"
            elif profile == "arm" and ARM_REGISTER.fullmatch(word.lower()):
                token = f"REG:{word.lower()}"
            elif (arm_keyword(word.lower()) if profile == "arm" else word in keywords):
                mnemonic = word.lower() if profile == "arm" else word
                token = f"K:{mnemonic}"
                control_count += mnemonic in CONTROL_WORDS
            else:
                token = f"{'LBL' if profile in {'marie', 'arm'} else 'ID'}:{word}"
                identifiers.add(word)
        elif kind == "string":
            token = "L:char" if value.startswith("'") else "L:string"
        elif kind == "number":
            if profile == "marie" and mnemonic == "skipcond" and value in {"000", "400", "800"}:
                token = f"K:skipcond/{value.lower()}"
            else:
                token = "L:number"
        else:
            token = f"P:{value}"
        tokens.append(token)
        lines.append(line)
        previous_line = line
        if len(tokens) > MAX_TOKENS:
            raise ValueError(f"El archivo supera el máximo de {MAX_TOKENS} tokens.")
    if not tokens:
        raise ValueError("El archivo no contiene código analizable.")
    return Lexed(tokens, lines, comments, identifiers, control_count)


def canonical(tokens: list[str]) -> tuple[str, ...]:
    symbols = {}
    normalized = []
    for token in tokens:
        if token.startswith(("ID:", "LBL:")):
            if token not in symbols:
                symbols[token] = f"{token.split(':', 1)[0]}:{len(symbols) + 1}"
            normalized.append(symbols[token])
        else:
            normalized.append(token)
    return tuple(normalized)


def analyze_style(lexed: Lexed) -> tuple[int, dict]:
    line_sizes = Counter(line for token, line in zip(lexed.tokens, lexed.lines) if token != "P:EOL")
    comments = [line for comment in lexed.comments for line in comment.splitlines()]
    explanatory_lines = sum(len(re.findall(r"[^\W\d]\w*", line)) >= 5 for line in comments)
    long_names = sum(len(name) >= 10 for name in lexed.identifiers)
    structured_comments = sum(
        bool(re.search(r"\b(?:params?|returns?|step|paso|ejemplo|example|description|descripci[oó]n)\b", line, re.I))
        for line in comments
    )
    lines = len(line_sizes)
    mean = sum(line_sizes.values()) / lines
    regularity = max(0.0, 1 - pstdev(line_sizes.values()) / mean) if lines >= 8 else 0.0
    features = [
        ("Comentarios explicativos", explanatory_lines, 28 * min(1, explanatory_lines / max(2, lines * 0.3))),
        ("Identificadores descriptivos", long_names, 24 * long_names / max(4, len(lexed.identifiers))),
        ("Palabras de control de flujo", lexed.control_count, 18 * min(1, lexed.control_count / max(3, lines * 0.25))),
        ("Comentarios con rótulo", structured_comments, 15 * min(1, structured_comments / 3)),
        ("Regularidad de longitud de línea", round(regularity, 3), 15 * regularity),
    ]
    sample_factor = min(1, len(lexed.tokens) / 100, lines / 10)
    return round(100 * sample_factor * sum(points for _, _, points in features)), {
        "code_lines": lines,
        "tokens": len(lexed.tokens),
        "sample_factor": round(sample_factor, 3),
        "features": [
            {"name": name, "observed": count, "points": round(points * sample_factor, 2)}
            for name, count, points in features
        ],
    }


def line_fragment(left: Prepared, right: Prepared, start_a: int, start_b: int, length: int) -> dict:
    return {
        "left": [left.lines[start_a], left.lines[start_a + length - 1]],
        "right": [right.lines[start_b], right.lines[start_b + length - 1]],
        "tokens": length,
    }


def anchor_pairs(left: list[int], right: list[int]):
    """Enumerate uncommon anchor matches and bound the cost of repeated ones."""
    if len(left) * len(right) <= MAX_ANCHOR_COMBINATIONS:
        yield from product(left, right)
        return
    yield from zip(left, right)
    # Nearby ranks keep a recurring anchor useful after an insertion or deletion.
    yield from zip(left[1:], right)
    yield from zip(left, right[1:])
    difference = len(left) - len(right)
    if difference > 0:
        yield from zip(left[difference:], right)
    elif difference < 0:
        yield from zip(left, right[-difference:])


def anchor_blocks(left: Prepared, right: Prepared):
    diagonals = defaultdict(list)
    for gram, positions in left.grams.items():
        matching = right.grams.get(gram)
        if not matching or not any(not token.startswith(("P:", "L:")) for token in gram):
            continue
        for start_a, start_b in anchor_pairs(positions, matching):
            diagonals[start_a - start_b].append(start_a)

    for offset, starts in diagonals.items():
        positions = sorted(set(starts))
        run_start = last = positions[0]
        for start in positions[1:]:
            if start > last + 1:
                yield run_start, run_start - offset, last + K - run_start
                run_start = start
            last = start
        yield run_start, run_start - offset, last + K - run_start


def claim_tiles(candidates, occupied_a, occupied_b, minimum):
    for start_a, start_b, length in sorted(candidates, key=lambda tile: (-tile[2], tile[0], tile[1])):
        # Recover free subranges when the end of a candidate overlaps a longer block.
        start = 0
        for offset in range(length + 1):
            if offset == length or occupied_a[start_a + offset] or occupied_b[start_b + offset]:
                if offset - start >= minimum:
                    size = offset - start
                    index_a, index_b = start_a + start, start_b + start
                    occupied_a[index_a:index_a + size] = occupied_b[index_b:index_b + size] = b"\x01" * size
                    yield index_a, index_b, size
                start = offset + 1


def gap_matches(left: Prepared, right: Prepared, blocks):
    ordered = sorted(blocks)
    for (start_a, start_b, length), (next_a, next_b, _) in zip(ordered, ordered[1:]):
        end_a, end_b = start_a + length, start_b + length
        size_a, size_b = next_a - end_a, next_b - end_b
        if not (2 <= size_a <= MAX_LOCAL_GAP and 2 <= size_b <= MAX_LOCAL_GAP):
            continue
        # Only inspect small gaps between two already established matches.
        gap_a = canonical(left.tokens[end_a:next_a])
        gap_b = canonical(right.tokens[end_b:next_b])
        matcher = SequenceMatcher(None, gap_a, gap_b, autojunk=False)
        for offset_a, offset_b, size in matcher.get_matching_blocks():
            if size >= 2 and any(not token.startswith("P:") for token in gap_a[offset_a:offset_a + size]):
                yield end_a + offset_a, end_b + offset_b, size


def fingerprint_dice(left: Prepared, right: Prepared) -> float:
    # Cap repeated windows so a mechanically repeated pattern does not dominate Dice.
    count_a = sum(min(len(starts), 4) for starts in left.grams.values())
    count_b = sum(min(len(starts), 4) for starts in right.grams.values())
    common = sum(min(len(starts), len(right.grams.get(gram, ())), 4) for gram, starts in left.grams.items())
    return 2 * common / (count_a + count_b)


def compare_in_order(left: Prepared, right: Prepared) -> tuple[int, dict, list[dict]]:
    size_a, size_b = len(left.tokens), len(right.tokens)
    short = min(size_a, size_b) < SHORT_SAMPLE_TOKENS
    equal = left.normalized == right.normalized
    if short:
        bp = 10000 if equal else 0
        return bp, {"short_sample": True, "dice_bp": bp, "coverage_bp": bp, "block_count": int(equal)}, (
            [line_fragment(left, right, 0, 0, size_a)] if equal else []
        )

    if equal:
        blocks = [(0, 0, size_a)]
        dice = 1.0
    else:
        occupied_a, occupied_b = bytearray(size_a), bytearray(size_b)
        blocks = list(claim_tiles(anchor_blocks(left, right), occupied_a, occupied_b, K))
        near_matches = list(gap_matches(left, right, blocks))
        blocks.extend(claim_tiles(near_matches, occupied_a, occupied_b, 2))
        dice = fingerprint_dice(left, right)

    covered = sum(length for _, _, length in blocks)
    coverage = 2 * covered / (size_a + size_b)
    score = 0.85 * coverage + 0.15 * dice
    longest = max((length for _, _, length in blocks), default=0)
    if covered >= MIN_CONTAINMENT_TOKENS and longest >= MIN_CONTAINMENT_BLOCK:
        score = max(score, 0.85 * covered / min(size_a, size_b))
    evidence = sorted(blocks, key=lambda tile: (-tile[2], tile[0], tile[1]))[:MAX_FRAGMENTS]
    return round(10000 * score), {
        "short_sample": False,
        "dice_bp": round(10000 * dice),
        "coverage_bp": round(10000 * coverage),
        "left_coverage_bp": round(10000 * covered / size_a),
        "right_coverage_bp": round(10000 * covered / size_b),
        "matched_tokens": covered,
        "block_count": len(blocks),
    }, [line_fragment(left, right, *block) for block in sorted(evidence)]


def compare(left: Prepared, right: Prepared) -> tuple[int, dict, list[dict]]:
    # Resolve greedy ties in a stable order regardless of which file was uploaded first.
    if left.normalized <= right.normalized:
        return compare_in_order(left, right)
    score, metrics, fragments = compare_in_order(right, left)
    if not metrics["short_sample"]:
        metrics["left_coverage_bp"], metrics["right_coverage_bp"] = (
            metrics["right_coverage_bp"], metrics["left_coverage_bp"],
        )
    for fragment in fragments:
        fragment["left"], fragment["right"] = fragment["right"], fragment["left"]
    return score, metrics, sorted(fragments, key=lambda fragment: (fragment["left"], fragment["right"]))
