@echo off
REM Gera o executavel no Windows (requer Python 3.10+ instalado)
python -m pip install --upgrade pyinstaller lxml openpyxl reportlab requests cryptography selenium pypdf
python -m PyInstaller --noconfirm --clean --onefile --windowed --name GeradorGuias --add-data "xsd;xsd" --collect-submodules reportlab.graphics.barcode --collect-data selenium --collect-submodules selenium --hidden-import app.portal_navegador --exclude-module PIL._avif --exclude-module PIL.AvifImagePlugin --exclude-module PIL._webp --exclude-module PIL.WebPImagePlugin --exclude-module PIL._imagingcms --exclude-module PIL.ImageCms --exclude-module lxml.objectify --exclude-module lxml.html --exclude-module selenium.webdriver.common.devtools GeradorGuias.py
python -m PyInstaller --noconfirm --clean --onefile --console --name GeradorGuias_cli --add-data "xsd;xsd" --collect-submodules reportlab.graphics.barcode --collect-data selenium --collect-submodules selenium --hidden-import app.portal_navegador --exclude-module PIL._avif --exclude-module PIL.AvifImagePlugin --exclude-module PIL._webp --exclude-module PIL.WebPImagePlugin --exclude-module PIL._imagingcms --exclude-module PIL.ImageCms --exclude-module lxml.objectify --exclude-module lxml.html --exclude-module selenium.webdriver.common.devtools GeradorGuias.py
echo Executaveis em dist\
pause
