// String templates and verbatim strings.
public string banner(string name, int count) {
    return @"user=$name count=$count";
}

public string verbatim() {
    return """no $interpolation here""";
}

public string page(string title) {
    return @"<html><body>$title</body></html>";
}
