// async workflows and task expressions.
module Demo.Async

open System.Threading.Tasks

let fetchOne (url: string) : Async<string> =
    async {
        return url
    }

let fetchAll (urls: string list) : Async<string list> =
    async {
        let mutable out = []
        for url in urls do
            let! value = fetchOne url
            out <- value :: out
        return List.rev out
    }

let runParallel (urls: string array) =
    urls |> Array.map (fun u -> fetchOne u) |> Async.Parallel
