package com.example;

import sys.FileSystem;

class Greeter {
    var name:String;

    public function greet(who:String):Void {
        helper(who);
    }

    static function helper(x:String):Void {
        trace(x);
    }
}

interface Runner {
    function run():Void;
}
