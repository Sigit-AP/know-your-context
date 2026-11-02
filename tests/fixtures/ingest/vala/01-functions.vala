// Basic functions with defaults and out/ref params.
int add(int a, int b) {
    return a + b;
}

string greet(string name, string greeting = "hello") {
    return @"$greeting, $name";
}

bool try_parse(string text, out int result) {
    result = 0;
    if (text.length == 0) {
        return false;
    }
    result = int.parse(text);
    return true;
}

void bump(ref int value) {
    value += 1;
}
