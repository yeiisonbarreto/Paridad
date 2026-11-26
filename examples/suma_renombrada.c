int calcular(const int tope)
{
  int resultado = 0;
  for (int valor = 1; valor <= tope; ++valor) {
    if ((valor % 2) == 0) { resultado += valor * valor; }
  }
  return resultado;
}
