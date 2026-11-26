/* Suma los cuadrados de los enteros pares entre 1 y el límite. */
int suma_pares(const int limite) {
    int acumulador = 0;
    for (int indice = 1; indice <= limite; ++indice) {
        if ((indice % 2) == 0) {
            acumulador += indice * indice;
        }
    }
    return acumulador;
}
