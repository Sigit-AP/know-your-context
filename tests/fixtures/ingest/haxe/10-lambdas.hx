// Closures and function values.
class Closures {
    public static function makeCounter(start:Int):Int->Int {
        var count = start;
        var step = function(amount:Int) {
            count += amount;
            return count;
        };
        return step;
    }

    public static function applyTwice(fn:Int->Int, x:Int):Int {
        return fn(fn(x));
    }

    public static function doubled(values:Array<Int>):Array<Int> {
        return values.map(function(v) { return v * 2; });
    }
}
