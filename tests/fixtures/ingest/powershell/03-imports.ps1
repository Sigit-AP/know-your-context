# using and Import-Module statements.
using namespace System.Collections.Generic
using module .\Helpers.psm1

Import-Module Microsoft.PowerShell.Utility

function Get-Stack {
    return [Stack[string]]::new()
}
