"""Explainable static rubric. Uploaded code is never executed."""

import re

WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
ENGLISH = set("""
a an the this that these those each every other use used using to of from for in into by
with without or and is are be can if then while when before after until as not than
add adds added sum sums total count counts return returns find finds check checks set sets
get gets start end stop load store read reads write writes print prints display input output
value values number numbers even odd result results initialize initial reset clear update
next previous current first last node list array index length size max min move copy
loop branch jump call save restore register registers instruction address memory stack
increment decrement calculate calculates compute computes process compare equal
function helper main test tests valid invalid error handle buffer signal clock state
enable disabled toggle changes bit bits mask shift rotate carry decrementing incrementing
sort sorted swap left right pack unpack direction port module wire combine select repeat
skip generate edge rising falling active high low default empty full convert keep protect
exit once running note example step parameter arguments positive
negative remove insert traverse already found done finish data setup case match
""".split())
SPANISH = set("""
el la los las un una unos unas este esta estas estos cada de del al para por con sin o y
es son si entonces mientras cuando antes despues hasta como no añade suma sumas retorna
retorno devuelve calcula comprueba verifica lee escribe guarda carga muestra imprime
entrada salida valor valores numero números número par impar resultado resultados
inicializa inicial reinicia actual actualiza primero ultimo último lista arreglo indice
índice tamaño mueve copia bucle salto llama registro direccion dirección pila funcion
función ejemplo paso parametros parámetros positivo negativo terminar datos caso
""".split())
PLACEHOLDER = re.compile(r"\b(?:TODO|FIXME|HACK)\b", re.IGNORECASE)
C_UNSAFE = {"ID:gets", "ID:strcpy", "ID:strcat", "ID:sprintf", "ID:atoi"}


def english_comment_fraction(comments: list[str]) -> tuple[int, int]:
    english = meaningful = 0
    for comment in comments:
        words = [word.casefold() for word in WORDS.findall(comment)]
        if not words:
            continue
        meaningful += 1
        english_count = sum(word in ENGLISH for word in words)
        spanish_count = sum(word in SPANISH for word in words)
        if len(words) >= 2 and english_count > spanish_count:
            english += 1
    return english, meaningful


def static_rubric(source, profile, lexed):
    lines = source.split("\n")
    code_lines = [lines[index - 1] for index in sorted(set(lexed.lines))]
    indents = [line[:len(line) - len(line.lstrip())] for line in code_lines]
    styles = {"tab" if "\t" in prefix else "space" for prefix in indents if prefix}
    mixed = any("\t" in prefix and " " in prefix for prefix in indents)
    indentation = 5 if len(styles) <= 1 and not mixed else 2
    width = round(5 * sum(len(line.expandtabs(4)) <= 100 for line in code_lines) / len(code_lines))
    organization = 5
    if profile in {"c", "verilog"}:
        depth = 0
        for token in lexed.tokens:
            depth += (token == "P:{") - (token == "P:}")
            if depth < 0:
                break
        organization = 5 if depth == 0 else 0
    elif len(code_lines) > 30:
        organization = 5 if "\n\n" in source else 3

    naming = round(5 * sum(len(name.strip("._")) > 1 for name in lexed.identifiers) / max(1, len(lexed.identifiers)))
    if not lexed.identifiers:
        naming = 5
    unfinished = sum(bool(PLACEHOLDER.search(comment)) for comment in lexed.comments)
    unsafe = len(C_UNSAFE.intersection(lexed.tokens)) if profile == "c" else 0
    practices = max(0, 5 - 2 * unsafe - min(3, unfinished)) + naming
    english, total = english_comment_fraction(lexed.comments)
    comments = round(10 * english / total) if total else 0
    return {
        "structure": indentation + width + organization,
        "practices": practices,
        "english_comments": comments,
        "comments_found": total,
        "comments_english": english,
    }
