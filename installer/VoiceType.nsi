; VoiceType installer (NSIS 3, Modern UI 2). Build with: python installer/build.py
; Copies the program files, then runs install.bat, which downloads Python, PyTorch,
; the libraries and the speech model. Installs per user: no admin rights needed, so the
; built-in auto-update can always write to the program folder.

Unicode true
!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define APP "VoiceType"
!define SRC ".."
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP}"
OutFile "${SRC}/VoiceType-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
BrandingText "${APP} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=0 "ProductName" "${APP}"
VIAddVersionKey /LANG=0 "FileDescription" "${APP} Setup"
VIAddVersionKey /LANG=0 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=0 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=0 "LegalCopyright" "github.com/brlk33/Voice-Type"

!include "MUI2.nsh"
!define MUI_ICON "${SRC}/voicetype.ico"
!define MUI_UNICON "${SRC}/voicetype.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "header.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "header.bmp"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TITLE "$(WelcomeTitle)"
!define MUI_WELCOMEPAGE_TEXT "$(WelcomeText)"
!insertmacro MUI_PAGE_WELCOME
!define MUI_COMPONENTSPAGE_SMALLDESC
!define MUI_COMPONENTSPAGE_TEXT_TOP "$(CompText)"
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_DIRECTORYPAGE_TEXT_TOP "$(DirText)"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "$(DoneTitle)"
!define MUI_FINISHPAGE_TEXT "$(DoneText)"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "$(RunNow)"
!define MUI_FINISHPAGE_RUN_FUNCTION RunApp
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"

LangString WelcomeTitle ${LANG_RUSSIAN} "Установка VoiceType ${VERSION}"
LangString WelcomeTitle ${LANG_ENGLISH} "VoiceType ${VERSION} Setup"
LangString WelcomeText ${LANG_RUSSIAN} "Диктовка и расшифровка речи прямо на вашем компьютере — без интернета, бесплатно и без ограничений.$\r$\n$\r$\nУстановщик скачает Python, PyTorch и модель распознавания речи. Понадобится:$\r$\n$\r$\n  •  около 1,3 ГБ на диске$\r$\n  •  интернет на 5–10 минут$\r$\n$\r$\nУскорение на видеокарте NVIDIA можно выбрать на следующем шаге или включить потом в настройках.$\r$\n$\r$\nПрава администратора не нужны."
LangString WelcomeText ${LANG_ENGLISH} "Dictation and speech transcription right on your computer — offline, free and unlimited.$\r$\n$\r$\nSetup will download Python, PyTorch and the speech model. You will need:$\r$\n$\r$\n  •  about 1.3 GB of disk space$\r$\n  •  internet for 5–10 minutes$\r$\n$\r$\nNVIDIA graphics card acceleration can be picked on the next step or turned on later in the settings.$\r$\n$\r$\nNo administrator rights needed."
LangString CompText ${LANG_RUSSIAN} "Базовая версия распознаёт речь на процессоре — для диктовки этого хватает. Ускорение на видеокарте можно добавить и позже: Настройки → Распознавание."
LangString CompText ${LANG_ENGLISH} "The base version recognizes speech on the processor — enough for dictation. Graphics card acceleration can also be added later: Settings → Recognition."
LangString SecMainName ${LANG_RUSSIAN} "VoiceType"
LangString SecMainName ${LANG_ENGLISH} "VoiceType"
LangString SecGpuName ${LANG_RUSSIAN} "Ускорение на видеокарте NVIDIA"
LangString SecGpuName ${LANG_ENGLISH} "NVIDIA graphics card acceleration"
LangString SecMainDesc ${LANG_RUSSIAN} "Программа, Python, PyTorch для процессора и модель распознавания GigaAM. Около 1,3 ГБ."
LangString SecMainDesc ${LANG_ENGLISH} "The app, Python, processor PyTorch and the GigaAM speech model. About 1.3 GB."
LangString SecGpuDesc ${LANG_RUSSIAN} "PyTorch для видеокарт NVIDIA: длинные записи и файлы распознаются в разы быстрее. Ещё ~3,7 ГБ на диске, скачивается ~2,5 ГБ."
LangString SecGpuDesc ${LANG_ENGLISH} "PyTorch for NVIDIA cards: long recordings and files are recognized several times faster. ~3.7 GB more on disk, ~2.5 GB download."
LangString DirText ${LANG_RUSSIAN} "Папка для VoiceType. Нужна папка, куда можно писать без прав администратора (не Program Files на диске C) — иначе не сработает автообновление."
LangString DirText ${LANG_ENGLISH} "Folder for VoiceType. Pick a folder you can write to without admin rights (not Program Files on drive C), otherwise auto-update can't work."
LangString NoWrite ${LANG_RUSSIAN} "Нет доступа на запись в папку:$\r$\n$INSTDIR$\r$\n$\r$\nВыберите другую папку."
LangString NoWrite ${LANG_ENGLISH} "Can't write to the folder:$\r$\n$INSTDIR$\r$\n$\r$\nPlease choose another folder."
LangString Step ${LANG_RUSSIAN} "Скачиваю и настраиваю компоненты. Это займёт 10–20 минут, окно может подолгу стоять на одной строке — это нормально."
LangString Step ${LANG_ENGLISH} "Downloading and setting up components. This takes 10–20 minutes; the window may sit on one line for a while — that's normal."
LangString Failed ${LANG_RUSSIAN} "Установка не завершилась.$\r$\n$\r$\nПодробности — в списке выше и в файле:$\r$\n$INSTDIR\logs\install.log$\r$\n$\r$\nМожно запустить установщик ещё раз: уже скачанное не будет качаться заново."
LangString Failed ${LANG_ENGLISH} "Setup did not finish.$\r$\n$\r$\nDetails are in the list above and in:$\r$\n$INSTDIR\logs\install.log$\r$\n$\r$\nYou can run setup again: what's already downloaded won't be downloaded again."
LangString DoneTitle ${LANG_RUSSIAN} "VoiceType установлен"
LangString DoneTitle ${LANG_ENGLISH} "VoiceType is installed"
LangString DoneText ${LANG_RUSSIAN} "Ярлык — на рабочем столе и в меню «Пуск».$\r$\n$\r$\nЗажмите правый Ctrl в любом окне и говорите — текст вставится сам.$\r$\n$\r$\nОбновления ставятся автоматически при запуске."
LangString DoneText ${LANG_ENGLISH} "Shortcuts are on the desktop and in the Start menu.$\r$\n$\r$\nHold Right Ctrl in any app and speak — the text is pasted for you.$\r$\n$\r$\nUpdates install automatically on launch."
LangString RunNow ${LANG_RUSSIAN} "Запустить VoiceType"
LangString RunNow ${LANG_ENGLISH} "Launch VoiceType"
LangString KeepData ${LANG_RUSSIAN} "Оставить ваши расшифровки, историю диктовки, словарь и настройки?$\r$\n$\r$\nДа — удалить только программу.$\r$\nНет — удалить всё."
LangString KeepData ${LANG_ENGLISH} "Keep your transcripts, dictation history, dictionary and settings?$\r$\n$\r$\nYes — remove only the program.$\r$\nNo — remove everything."

!macro KillApp
  ; the app, its editor server (llama.cpp) and anything else started from the program folder
  nsExec::Exec `powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $$_.ExecutablePath -like '$INSTDIR\*' -or $$_.CommandLine -like '*$INSTDIR\app\app.py*' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force }"`
  Pop $0
  Sleep 800
!macroend

Section "$(SecMainName)" SecMain
  SectionIn RO
  AddSize 1300000
  SetOutPath "$INSTDIR"
  ClearErrors
  FileOpen $0 "$INSTDIR\.write_test" w
  IfErrors 0 +3
    MessageBox MB_ICONSTOP "$(NoWrite)"
    Abort
  FileClose $0
  Delete "$INSTDIR\.write_test"

  !insertmacro KillApp

  ; program files (user data in the same folder is never touched)
  File /r /x __pycache__ "${SRC}/app"
  File /r "${SRC}/ui"
  File "${SRC}/install.bat"
  File "${SRC}/update.bat"
  File "${SRC}/start.bat"
  File "${SRC}/start_debug.bat"
  File "${SRC}/transcribe_files.bat"
  File "${SRC}/build_exe.bat"
  File "${SRC}/requirements.txt"
  File "${SRC}/constraints.txt"
  File "${SRC}/version.txt"
  File "${SRC}/README.md"
  File "${SRC}/dictionary.default.txt"
  File "${SRC}/voicetype.ico"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
SectionEnd

Section /o "$(SecGpuName)" SecGpu
  AddSize 3700000
SectionEnd

Section "-Setup"
  StrCpy $1 ""
  SectionGetFlags ${SecGpu} $2
  IntOp $2 $2 & ${SF_SELECTED}
  StrCmp $2 "0" +2
    StrCpy $1 " /gpu"
  DetailPrint "$(Step)"
  nsExec::ExecToLog '"$SYSDIR\cmd.exe" /c ""$INSTDIR\install.bat" /silent$1"'
  Pop $0
  StrCmp $0 "0" ok
    SetDetailsView show
    MessageBox MB_ICONSTOP "$(Failed)"
    Abort
  ok:

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "https://github.com/brlk33/Voice-Type"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\.venv\Scripts\VoiceType.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 1300000
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "$(SecMainDesc)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecGpu} "$(SecGpuDesc)"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function RunApp
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\.venv\Scripts\VoiceType.exe" "$INSTDIR\app\app.py"'
FunctionEnd

Section "Uninstall"
  !insertmacro KillApp
  Delete "$DESKTOP\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$SMSTARTUP\${APP}.lnk"

  MessageBox MB_YESNO|MB_ICONQUESTION "$(KeepData)" IDYES keep
    RMDir /r "$INSTDIR"
    Goto cleaned
  keep:
    RMDir /r "$INSTDIR\app"
    RMDir /r "$INSTDIR\ui"
    RMDir /r "$INSTDIR\.venv"
    RMDir /r "$INSTDIR\tools"
    RMDir /r "$INSTDIR\cache"
    RMDir /r "$INSTDIR\models"
    RMDir /r "$INSTDIR\logs"
    Delete "$INSTDIR\*.bat"
    Delete "$INSTDIR\requirements.txt"
    Delete "$INSTDIR\constraints.txt"
    Delete "$INSTDIR\version.txt"
    Delete "$INSTDIR\README.md"
    Delete "$INSTDIR\README.txt"
    Delete "$INSTDIR\dictionary.default.txt"
    Delete "$INSTDIR\voicetype.ico"
    Delete "$INSTDIR\Uninstall.exe"
  cleaned:

  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APP}"
SectionEnd
