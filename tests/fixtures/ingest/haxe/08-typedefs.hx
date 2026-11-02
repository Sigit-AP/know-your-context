// Typedefs over primitives and structures (abstract-free).
typedef UserId = Int;

typedef Credentials = {
    var user:String;
    var token:String;
}

typedef Handler = Int->String;

class Registry {
    public static function make(user:String, token:String):Credentials {
        return { user: user, token: token };
    }

    public static function sizeOf(creds:Credentials):Int {
        return creds.user.length + creds.token.length;
    }
}
