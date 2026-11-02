// try/with/finally, Result and custom exceptions.
module Demo.Errors

exception ConfigError of string

let readConfig (map: Map<string, string>) (key: string) : string =
    match Map.tryFind key map with
    | Some value -> value
    | None -> raise (ConfigError key)

let readSafe (map: Map<string, string>) (key: string) : Result<string, string> =
    try
        Ok(readConfig map key)
    with
    | ConfigError k -> Error k
    | ex -> Error ex.Message

let guarded (fn: unit -> int) =
    try
        fn ()
    finally
        ()
