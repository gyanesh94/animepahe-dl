# Renaming the files from all directories in the given folder

from operator import add
import os
import re

BASE_DIR = ["./"]

def renameFiles():
    while BASE_DIR:
        baseDir = BASE_DIR.pop(0)
        fileList = list(filter(lambda f: not f.startswith(".") , os.listdir(baseDir)))
        totalFiles = len(fileList)
        zeroFill = len(str(totalFiles))
        for fileOrDir in fileList:
            fileOrDirPath = os.path.join(baseDir, fileOrDir)
            if os.path.exists(fileOrDirPath) and os.path.isdir(fileOrDirPath):
                BASE_DIR.append(fileOrDirPath)
                continue
            matches = re.findall(r'[0-9]+\.mp4', fileOrDir)
            if len(matches) == 1:
                num = re.findall(r'\d+', matches[0])[0].zfill(zeroFill)
                newName = re.sub(r' [0-9]+\.', f' {num}.', fileOrDir)
                newPath = os.path.join(baseDir, newName)
                os.rename(fileOrDirPath, newPath)

renameFiles()
