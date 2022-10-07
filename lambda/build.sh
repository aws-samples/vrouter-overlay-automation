#!/bin/bash

PATH=$PATH
cwd=`pwd`

for function in GetIps vRouterInterfaces; 
do 
  cd "$cwd";
  cd "$function"/src/;
  pip install -r requirements.txt -t .;
  zip -r ../../"$function".zip *;
done
