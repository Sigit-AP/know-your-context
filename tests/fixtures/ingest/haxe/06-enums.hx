// Enums with constructors and pattern matching.
enum Colour {
    Red;
    Green;
    Blue;
}

enum Msg {
    Quit;
    Move(x:Int, y:Int);
    Write(text:String);
}

class Weights {
    public static function weight(msg:Msg):Int {
        var out = 0;
        switch (msg) {
            case Quit:
                out = 0;
            case Move(x, y):
                out = (x == y) ? 3 : 2;
            case Write(text):
                out = text.length;
        }
        return out;
    }
}
