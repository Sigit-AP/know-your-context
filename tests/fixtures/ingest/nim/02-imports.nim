## Import forms: plain, multi and selective.
import strutils
import std/tables
import sequtils, strformat
from os import fileExists
import ./localmod

proc boot(): string =
  let t = initTable[string, string]()
  result = t.len.intToStr
