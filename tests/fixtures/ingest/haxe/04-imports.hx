// Import forms: plain, wildcard, alias via as.
import haxe.io.Path;
import sys.FileSystem;
import sys.io.File;
import haxe.ds.StringMap;
import Math.max as maximum;

class Imports {
    public static function describe(dir:String):String {
        return Path.directory(dir);
    }
}
