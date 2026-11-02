// open directives for namespaces and modules.
module Demo.Imports

open System
open System.IO
open System.Text

let describe (name: string) =
    let builder = StringBuilder()
    builder.Append(name).ToString()
