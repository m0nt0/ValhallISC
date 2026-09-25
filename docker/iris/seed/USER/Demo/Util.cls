/// Utility class methods used by the IRISFS tests.
Class Demo.Util
{

ClassMethod Hello(name As %String = "World") As %String
{
    quit "Hello, "_name_"!"
}

ClassMethod Add(a As %Integer, b As %Integer) As %Integer
{
    quit a + b
}

}
