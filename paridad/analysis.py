"""Static lexical analysis. Uploaded code is treated entirely as text."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import pstdev

from .config import MAX_TOKENS

ANALYSIS_VERSION = "lexical-1"
K = 8
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

    @classmethod
    def from_analysis(cls, stored: dict):
        tokens, lines = stored["tokens"], stored["lines"]
        grams = defaultdict(list)
        for i in range(max(0, len(tokens) - K + 1)):
            grams[canonical(tokens[i:i + K])].append(i)
        return cls(tokens, lines, grams)


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


def compare(left: Prepared, right: Prepared) -> tuple[int, dict, list[dict]]:
    size_a, size_b = len(left.tokens), len(right.tokens)
    if min(size_a, size_b) < K:
        equal = canonical(left.tokens) == canonical(right.tokens)
        bp = 10000 if equal else 0
        return bp, {"short_sample": True, "dice_bp": bp, "coverage_bp": bp, "block_count": int(equal)}, (
            [line_fragment(left, right, 0, 0, size_a)] if equal else []
        )

    diagonals = defaultdict(list)
    common = 0
    for gram, left_positions in left.grams.items():
        right_positions = right.grams.get(gram, ())
        common += min(len(left_positions), len(right_positions))
        for start_a, start_b in zip(left_positions, right_positions):
            diagonals[start_a - start_b].append(start_a)

    candidates = []
    for offset, starts in diagonals.items():
        positions = sorted(starts)
        run_start = last_start = positions[0]
        for start in positions[1:]:
            if start > last_start + 1:
                candidates.append((last_start + K - run_start, run_start, run_start - offset))
                run_start = start
            last_start = start
        candidates.append((last_start + K - run_start, run_start, run_start - offset))

    occupied_a, occupied_b = bytearray(size_a), bytearray(size_b)
    coverage = 0
    blocks = []
    for length, start_a, start_b in sorted(candidates, key=lambda block: (-block[0], block[1], block[2])):
        end_a, end_b = start_a + length, start_b + length
        if any(occupied_a[start_a:end_a]) or any(occupied_b[start_b:end_b]):
            continue
        occupied_a[start_a:end_a] = occupied_b[start_b:end_b] = b"\x01" * length
        coverage += length
        blocks.append((start_a, start_b, length))

    dice = 2 * common / (size_a + size_b - 2 * K + 2)
    token_coverage = 2 * coverage / (size_a + size_b)
    return round(10000 * (0.7 * dice + 0.3 * token_coverage)), {
        "short_sample": False,
        "dice_bp": round(10000 * dice),
        "coverage_bp": round(10000 * token_coverage),
        "block_count": len(blocks),
    }, [line_fragment(left, right, *block) for block in sorted(blocks[:MAX_FRAGMENTS])]
