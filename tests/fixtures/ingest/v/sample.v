module main

import os
import strings

struct Greeter {
    name string
}

fn (g Greeter) greet(who string) {
    helper(who)
}

fn helper(x string) {
    println(x)
}

const limit = 42
