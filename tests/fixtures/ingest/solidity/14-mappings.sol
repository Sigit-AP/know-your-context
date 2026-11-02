// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Ledger {
    mapping(address => uint256) public balances;
    mapping(address => mapping(address => uint256)) public allowances;

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }

    function approve(address spender, uint256 amount) external {
        allowances[msg.sender][spender] = amount;
    }

    function balanceOf(address who) external view returns (uint256) {
        return balances[who];
    }
}
