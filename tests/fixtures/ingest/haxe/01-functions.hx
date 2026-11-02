// Static functions with defaults and optional args.
class Functions {
    public static function add(a:Int, b:Int):Int {
        return a + b;
    }

    public static function greet(name:String, ?greeting:String = "hello"):String {
        return greeting + ", " + name;
    }

    static function variadic(first:Int):Int {
        return first;
    }
}
