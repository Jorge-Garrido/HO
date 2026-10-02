Archivos Base que todos los modulos usan.

Config.py 

  Todas las reglas de negocio: severidad por campo, formatos válidos, códigos recurrentes, tolerancias, autoridad de  │
  la EPS, OCR activo o no.

Apuntes del codgio 
  Presenta clases Pydantic para establecer las reglas de negocio 

  class ReglaCampo - fundamental
  Presenta las reglas que se le va a dar a los campos en situaciones, esta es una clase fundamental dentro del codigo 
  
  class ReglasTarifa
  Presenta las reglas de las tarifas estan deben ser ajustadas segun lo que se necesita
  
  
¿Preguntras a la IA? 
  Como se podria cambiar por un TOML y quue conviene mas
  Para que se usa __future__ 
  Por que se usa pydantic que ventaja tiene usarlo
  Por que la logica de las ReglaCampo y que mas se podria llegar a meter ahi segun el decreto
  Como se estiupilo los valores minimos como score_minimo y margen_minimo, hacen referencia a reglas de B NLP?
  Como generalizar los codigos recurrentes o que hacer para que no esten hardcodeados.
  lo mismo para los dian_codigos_aceptados
  Como se puede lograr que los ejemplos de fecha con aviso validar si tienen multiples fechas o que parametrizacion hay.
  Como se esta manejando actualmente la extraccion_pdf y en que casos se usa plantilla o generica 
  Porque asume que el documento de la EPS es la autoridad para ciertos campos si su lectura es confiable si ellos tambien desde el consultorio se pueden estar equivocando y para cuales. 
  en produccion como va a funcionar la funcion de cargar.

  Hacer una documentacion de este archiuvo que sea facil de entender con temas tecnicos y todo con las diferentes reglas de negocio para saber que hace falta. 


-------------------------------------------------------------------------------------------------------------------------------

Schemas.py 

  Modelos de datos y que se extrajo por documento y resultado de factura(El json que se guarda al final, potencialmente excel)
  

Preguntas a la IA 

  Preguntar si el modelado de datos corresponde al decreto y cual hace falta y cual no 
  Revisar codigo de validacion respecto al decreto 
  Como se estipulan las propuesta de correcciones estan predefinidas 
  En hallazgo que significa que sea una verificacion que no es un campo comparado
  Arma una presentacion de la automatizacion usando este modelado de datos, con nombres de las clases y todo lo necesario. 


-------------------------------------------------------------------------------------------------------------------------------


normalize.py 

  Limpieza de valores: mayúsculas, tildes, NIT sin dígito de verificación, montos en los dos formatos, fechas, redondeo como Excel.
r Preprocesamiento: 
    -quitar las tildes 
    -el codigo cups se convierte a texto nunca se trata como numero
    -le quita el numero de verifivacion al nit 
    -quita los caracteres raros del jombre


Preguntas a la IA 

  Esas stopwords se eliminan del documento que pasa si depronto son un error de escritura de o de layour de pdf.
  Revisar en el decreto si el cups si es de 5 dígitos 
  Que preprocesamiento le hace a la cedula
  Es necesario volver a la raiz? por que esto conviene 
  Que formato de dinero esta dejando y como sabe que no se llega a confundir con la cedula
  Por que decide el criterio de redondeo y cuando se hace 



--------------------------------------------------------------------------------------------------------------------------------


compare.py 

  Comparadores clasicos de procesamiento de lenguaje natural 


Preguntas IA 

  En distancia ocr que criterio se usa 
  Embedigns puede lleagr a significar algo si se quiere tener en cuenta el contexto? por ejemplo en los casos en que se quieren comparar lineas o muiltiples servicxios saber si se esrta hablando de lo mismo o con validaciones clasicas se puede llegar a algo mejor, ademas algun enfoque de rag puede llegar a funcionar aca?
  
  
--------------------------------------------------------------------------------------------------------------------------------- 
layout.py

  












