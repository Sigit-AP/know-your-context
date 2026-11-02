# enums and class inheritance.
enum Level {
    Low = 1
    High = 2
}

class Animal {
    [string] Speak() {
        return "..."
    }
}

class Dog : Animal {
    [string] Speak() {
        return "woof"
    }
}
