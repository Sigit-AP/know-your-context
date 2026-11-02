// String interpolation and formatting.
class Render {
    public static function page(title:String):String {
        return '<html><body>${title}</body></html>';
    }

    public static function banner(name:String, count:Int):String {
        return 'user=$name count=$count';
    }
}
