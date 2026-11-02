// Active patterns: total, partial and parameterized.
module Demo.ActivePatterns

let (|Even|Odd|) (value: int) =
    if value % 2 = 0 then Even else Odd

let (|ParseInt|_|) (text: string) =
    match System.Int32.TryParse text with
    | true, n -> Some n
    | _ -> None

let describe (value: int) =
    match value with
    | Even -> "even"
    | Odd -> "odd"
